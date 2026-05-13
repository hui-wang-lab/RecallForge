"""Ingest-specific exceptions with structured diagnostics."""

from __future__ import annotations

from typing import Any


class IngestError(Exception):
    phase = "ingest"

    def diagnostics(self) -> dict[str, Any]:
        return {"error_phase": self.phase, "error_type": type(self).__name__}


class UnsupportedFileTypeError(IngestError):
    phase = "preflight"

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix
        super().__init__(f"Unsupported file type: {suffix or '<none>'}")

    def diagnostics(self) -> dict[str, Any]:
        return {**super().diagnostics(), "suffix": self.suffix}


class OversizeError(IngestError):
    phase = "preflight"

    def __init__(
        self,
        message: str,
        limit_name: str,
        actual: int,
        limit: int,
        phase: str = "preflight",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.limit_name = limit_name
        self.actual = actual
        self.limit = limit
        self.phase = phase

    def diagnostics(self) -> dict[str, Any]:
        return {
            **super().diagnostics(),
            "limit_breached": self.limit_name,
            "actual": self.actual,
            "limit": self.limit,
        }


class ParserUnavailableError(IngestError):
    phase = "parse"


class ChunkKeyConflictError(IngestError):
    phase = "adapter"

    def __init__(self, duplicate_parent_keys: list[str], duplicate_child_keys: list[str]) -> None:
        self.duplicate_parent_keys = duplicate_parent_keys
        self.duplicate_child_keys = duplicate_child_keys
        super().__init__(
            f"ChunkFlow produced duplicate keys: parent={duplicate_parent_keys}, child={duplicate_child_keys}"
        )

    def diagnostics(self) -> dict[str, Any]:
        return {
            **super().diagnostics(),
            "chunk_key_conflicts": {
                "duplicate_parent_keys": self.duplicate_parent_keys,
                "duplicate_child_keys": self.duplicate_child_keys,
            },
        }
