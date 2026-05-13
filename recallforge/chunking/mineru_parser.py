"""MinerU-based parser for high-fidelity PDF extraction."""
from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

try:
    import requests
except ImportError:  # pragma: no cover - exercised only in minimal envs
    requests = None  # type: ignore[assignment]

from recallforge.chunking.tokenizer import estimate_tokens

logger = logging.getLogger("recallforge.chunking.mineru_parser")

MINERU_BASE_URL = "https://mineru.net"
_POLLING_STATES = {"waiting-file", "uploading", "pending", "running", "converting"}


def is_mineru_available() -> bool:
    """Return whether the MinerU precise API is configured."""
    return bool(os.getenv("MINERU_API_TOKEN"))


def parse_pdf_with_mineru(
    pdf_path: str | Path,
    max_tokens: int = 400,
    *,
    timeout_seconds: Optional[int] = None,
    poll_interval_seconds: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Parse a local PDF through MinerU precise API and normalize to chunks.

    Returns dictionaries compatible with ``parse_pdf_with_docling``:
    raw_text, page_number, chapter, section, domain_hint, headings, content_type.
    """
    full_md, content_list = parse_pdf_with_mineru_artifacts(
        pdf_path,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    chunks = _content_list_to_chunks(content_list, max_tokens=max_tokens)
    if not chunks and full_md.strip():
        chunks = _markdown_to_chunks(full_md, max_tokens=max_tokens)

    logger.info("MinerU parsed %s: %d chunks", pdf_path, len(chunks))
    return chunks


def parse_pdf_with_mineru_artifacts(
    pdf_path: str | Path,
    *,
    timeout_seconds: Optional[int] = None,
    poll_interval_seconds: Optional[float] = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Parse a local PDF through MinerU and return raw markdown + content list."""
    token = os.getenv("MINERU_API_TOKEN")
    if not token:
        raise RuntimeError("MINERU_API_TOKEN is not configured")
    _ensure_requests()

    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {pdf_path}")

    timeout = timeout_seconds or _env_int("MINERU_POLL_TIMEOUT_SECONDS", 300)
    interval = poll_interval_seconds or _env_float("MINERU_POLL_INTERVAL_SECONDS", 3.0)

    batch_id, upload_url = _create_upload_task(token, path.name)
    _upload_file(upload_url, path)
    result = _poll_batch_result(token, batch_id, timeout, interval)
    zip_url = result.get("full_zip_url")
    if not zip_url:
        raise RuntimeError("MinerU finished without full_zip_url")

    with tempfile.TemporaryDirectory(prefix="chunkflow_mineru_") as tmp:
        archive_path = Path(tmp) / _zip_name_from_url(zip_url)
        _download_file(zip_url, archive_path)
        full_md, content_list = _read_mineru_zip(archive_path)

    return full_md, content_list


def _create_upload_task(token: str, file_name: str) -> tuple[str, str]:
    _ensure_requests()
    url = f"{MINERU_BASE_URL}/api/v4/file-urls/batch"
    data: dict[str, Any] = {
        "files": [{"name": file_name, "data_id": uuid.uuid4().hex}],
        "model_version": os.getenv("MINERU_MODEL_VERSION", "vlm"),
        "enable_table": _env_bool("MINERU_ENABLE_TABLE", True),
        "enable_formula": _env_bool("MINERU_ENABLE_FORMULA", True),
        "is_ocr": _env_bool("MINERU_IS_OCR", False),
        "language": os.getenv("MINERU_LANGUAGE", "ch"),
    }
    response = requests.post(url, headers=_auth_headers(token), json=data, timeout=30)
    payload = _json_or_raise(response)
    data = payload.get("data") or {}
    batch_id = data.get("batch_id")
    file_urls = data.get("file_urls") or []
    if not batch_id or not file_urls:
        raise RuntimeError(f"MinerU upload task response missing batch_id/file_urls: {payload}")
    return str(batch_id), str(file_urls[0])


def _upload_file(upload_url: str, pdf_path: Path) -> None:
    _ensure_requests()
    with pdf_path.open("rb") as f:
        response = requests.put(upload_url, data=f, timeout=120)
    if response.status_code // 100 != 2:
        raise RuntimeError(f"MinerU file upload failed: HTTP {response.status_code}")


def _poll_batch_result(
    token: str,
    batch_id: str,
    timeout_seconds: int,
    interval_seconds: float,
) -> dict[str, Any]:
    _ensure_requests()
    url = f"{MINERU_BASE_URL}/api/v4/extract-results/batch/{batch_id}"
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        response = requests.get(url, headers=_auth_headers(token), timeout=30)
        payload = _json_or_raise(response)
        results = (payload.get("data") or {}).get("extract_result") or []
        if results:
            result = results[0]
            state = result.get("state")
            if state == "done":
                return result
            if state == "failed":
                raise RuntimeError(result.get("err_msg") or "MinerU parsing failed")
            if state not in _POLLING_STATES:
                raise RuntimeError(f"Unexpected MinerU task state: {state}")
        time.sleep(interval_seconds)

    raise TimeoutError(f"MinerU parsing timed out after {timeout_seconds} seconds")


def _download_file(url: str, target: Path) -> None:
    _ensure_requests()
    errors: list[str] = []
    for attempt in range(3):
        try:
            with requests.get(url, stream=True, timeout=120) as response:
                if response.status_code // 100 != 2:
                    raise RuntimeError(f"HTTP {response.status_code}")
                with target.open("wb") as f:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
            return
        except Exception as exc:
            errors.append(str(exc))
            if target.exists():
                target.unlink()
            time.sleep(1 + attempt)

    if _download_file_with_wsl_curl(url, target):
        return

    raise RuntimeError(f"MinerU result download failed: {'; '.join(errors)}")


def _download_file_with_wsl_curl(url: str, target: Path) -> bool:
    """Fallback for Windows TLS/CDN issues observed with MinerU result URLs."""
    try:
        wsl_target = subprocess.check_output(
            ["wsl", "-e", "wslpath", "-a", str(target)],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).strip()
        result = subprocess.run(
            [
                "wsl",
                "-e",
                "bash",
                "-lc",
                'curl -L --fail --retry 3 --retry-delay 2 -o "$1" "$2"',
                "_",
                wsl_target,
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0 and target.exists() and target.stat().st_size > 0


def _read_mineru_zip(archive_path: Path) -> tuple[str, list[dict[str, Any]]]:
    full_md = ""
    content_list: list[dict[str, Any]] = []
    with zipfile.ZipFile(archive_path) as zf:
        names = zf.namelist()
        md_name = _find_zip_member(names, "full.md")
        content_name = next((n for n in names if n.endswith("_content_list.json")), None)

        if md_name:
            full_md = zf.read(md_name).decode("utf-8", errors="replace")
        if content_name:
            raw = zf.read(content_name).decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                content_list = [x for x in parsed if isinstance(x, dict)]

    return full_md, content_list


def _content_list_to_chunks(
    items: list[dict[str, Any]],
    *,
    max_tokens: int,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    heading_stack: list[str] = []
    pending_text: list[str] = []
    pending_page: Optional[int] = None
    pending_type = "text"

    def flush() -> None:
        nonlocal pending_text, pending_page, pending_type
        text = "\n\n".join(t for t in pending_text if t.strip()).strip()
        if text:
            chunks.append(
                _make_chunk_dict(
                    text,
                    page_number=pending_page or 1,
                    headings=heading_stack,
                    content_type=pending_type,
                )
            )
        pending_text = []
        pending_page = None
        pending_type = "text"

    for item in items:
        content_type = str(item.get("type") or item.get("content_type") or "text").lower()
        page_number = _page_number_from_item(item)

        if content_type in {"title", "heading"}:
            flush()
            title = _item_text(item).strip()
            if title:
                level = _heading_level(item)
                heading_stack = heading_stack[: max(level - 1, 0)]
                heading_stack.append(title)
                chunks.append(
                    _make_chunk_dict(
                        title,
                        page_number=page_number,
                        headings=heading_stack,
                        content_type="title",
                    )
                )
            continue

        if content_type == "table":
            flush()
            table_text = _table_text(item)
            if table_text.strip():
                chunks.append(
                    _make_chunk_dict(
                        table_text,
                        page_number=page_number,
                        headings=heading_stack,
                        content_type="table",
                    )
                )
            continue

        text = _item_text(item).strip()
        if not text:
            continue

        next_tokens = estimate_tokens("\n\n".join([*pending_text, text]))
        if pending_text and next_tokens > max_tokens:
            flush()
        pending_text.append(text)
        pending_page = pending_page or page_number
        pending_type = _normal_content_type(content_type)

    flush()
    return chunks


def _markdown_to_chunks(markdown: str, *, max_tokens: int) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    headings: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            chunks.append(_make_chunk_dict(text, page_number=1, headings=headings, content_type="text"))
        buffer.clear()

    for block in re.split(r"\n{2,}", markdown):
        stripped = block.strip()
        if not stripped:
            continue
        m = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if m:
            flush()
            level = len(m.group(1))
            headings = headings[: max(level - 1, 0)]
            headings.append(m.group(2).strip())
            chunks.append(_make_chunk_dict(stripped, page_number=1, headings=headings, content_type="title"))
            continue
        if buffer and estimate_tokens("\n\n".join([*buffer, stripped])) > max_tokens:
            flush()
        buffer.append(stripped)

    flush()
    return chunks


def _make_chunk_dict(
    raw_text: str,
    *,
    page_number: int,
    headings: list[str],
    content_type: str,
) -> dict[str, Any]:
    chapter = headings[0] if headings else None
    section = headings[-1] if headings else None
    return {
        "raw_text": raw_text,
        "page_number": page_number,
        "chapter": chapter,
        "section": section,
        "domain_hint": None,
        "headings": list(headings),
        "content_type": content_type,
    }


def _table_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    caption = item.get("table_caption") or item.get("caption")
    if isinstance(caption, list):
        caption = " ".join(str(x) for x in caption)
    if caption:
        parts.append(str(caption).strip())

    body = (
        item.get("table_body")
        or item.get("html")
        or item.get("table_html")
        or item.get("text")
        or item.get("md")
        or item.get("markdown")
    )
    if isinstance(body, str):
        parts.append(_html_table_to_markdown(body) if "<table" in body.lower() else body.strip())
    elif isinstance(body, list):
        parts.append(_matrix_to_markdown(body))
    elif isinstance(body, dict):
        parts.append(json.dumps(body, ensure_ascii=False))

    footnote = item.get("table_footnote") or item.get("footnote")
    if isinstance(footnote, list):
        footnote = " ".join(str(x) for x in footnote)
    if footnote:
        parts.append(str(footnote).strip())

    return "\n\n".join(p for p in parts if p)


def _item_text(item: dict[str, Any]) -> str:
    for key in ("text", "content", "md", "markdown"):
        value = item.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(str(v) for v in value)
    return ""


def _page_number_from_item(item: dict[str, Any]) -> int:
    for key in ("page_idx", "page_index"):
        value = item.get(key)
        if isinstance(value, int):
            return value + 1
    for key in ("page", "page_no", "page_number"):
        value = item.get(key)
        if isinstance(value, int):
            return value
    return 1


def _heading_level(item: dict[str, Any]) -> int:
    value = item.get("text_level") or item.get("level")
    if isinstance(value, int):
        return max(1, min(value, 6))
    return 1


def _normal_content_type(content_type: str) -> str:
    if content_type in {"list", "list_item"}:
        return "list_item"
    if "equation" in content_type or "formula" in content_type:
        return "formula"
    return "text"


class _SimpleTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: Optional[list[str]] = None
        self._current_cell: Optional[list[str]] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_row is not None and self._current_cell is not None:
            self._current_row.append(" ".join("".join(self._current_cell).split()))
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = None


def _html_table_to_markdown(value: str) -> str:
    parser = _SimpleTableParser()
    parser.feed(value)
    if not parser.rows:
        return html.unescape(re.sub(r"<[^>]+>", " ", value)).strip()
    return _matrix_to_markdown(parser.rows)


def _matrix_to_markdown(rows: list[Any]) -> str:
    matrix = [[str(cell).strip() for cell in row] for row in rows if isinstance(row, list)]
    if not matrix:
        return ""
    width = max(len(row) for row in matrix)
    matrix = [row + [""] * (width - len(row)) for row in matrix]
    header = matrix[0]
    body = matrix[1:] or [[""] * width]
    lines = [
        "| " + " | ".join(_escape_md_cell(cell) for cell in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(_escape_md_cell(cell) for cell in row) + " |" for row in body)
    return "\n".join(lines)


def _escape_md_cell(value: str) -> str:
    return value.replace("\n", " ").replace("|", r"\|")


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }


def _json_or_raise(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"MinerU returned non-JSON response: HTTP {response.status_code}") from exc
    if response.status_code // 100 != 2:
        raise RuntimeError(f"MinerU HTTP {response.status_code}: {payload}")
    if payload.get("code") != 0:
        raise RuntimeError(f"MinerU API error: {payload.get('msg') or payload}")
    return payload


def _ensure_requests() -> None:
    if requests is None:
        raise RuntimeError(
            "MinerU parser requires requests. Install dependencies with: pip install -r requirements.txt"
        )


def _find_zip_member(names: list[str], suffix: str) -> Optional[str]:
    return next((n for n in names if n.endswith(suffix)), None)


def _zip_name_from_url(url: str) -> str:
    name = Path(urlparse(url).path).name
    return name if name.endswith(".zip") else "mineru_result.zip"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default
