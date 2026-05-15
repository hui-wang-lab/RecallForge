"""Minimal debug console routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from recallforge.api.auth import AuthenticatedRequest, require_scopes
from recallforge.api.dependencies import get_settings
from recallforge.api.errors import ApiError, ResourceNotFoundError
from recallforge.config import Settings

router = APIRouter(tags=["console"])
STATIC_ROOT = Path(__file__).resolve().parents[2] / "console" / "static"


@router.get("/console", summary="RecallForge debug console")
async def console_index(
    auth: AuthenticatedRequest = Depends(require_scopes("console:use")),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    _ensure_enabled(settings)
    return FileResponse(STATIC_ROOT / "index.html", media_type="text/html")


@router.get("/console/{asset_path:path}", summary="RecallForge debug console asset")
async def console_asset(
    asset_path: str,
    auth: AuthenticatedRequest = Depends(require_scopes("console:use")),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    _ensure_enabled(settings)
    path = (STATIC_ROOT / asset_path).resolve()
    if STATIC_ROOT.resolve() not in path.parents or not path.is_file():
        raise ResourceNotFoundError("console asset not found")
    media_type = "text/css" if path.suffix == ".css" else "application/javascript"
    return FileResponse(path, media_type=media_type)


def _ensure_enabled(settings: Settings) -> None:
    if not settings.console_enabled:
        raise ApiError("console_disabled", "console is disabled", 404)
