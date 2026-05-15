"""Health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from recallforge.api.dependencies import get_settings
from recallforge.api.schemas import HealthResponse
from recallforge.config import Settings

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse, summary="Process health")
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse, summary="API readiness")
async def readyz(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(status="ready" if settings.api_enabled else "disabled")
