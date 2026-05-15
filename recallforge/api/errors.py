"""HTTP-facing error types for the M5 Knowledge API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApiError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class AuthenticationError(ApiError):
    def __init__(self, message: str = "authentication required", details: dict[str, Any] | None = None) -> None:
        super().__init__("unauthorized", message, 401, details or {})


class PermissionDeniedError(ApiError):
    def __init__(self, message: str = "insufficient scope", details: dict[str, Any] | None = None) -> None:
        super().__init__("forbidden", message, 403, details or {})


class ValidationApiError(ApiError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(code, message, 400, details or {})


class ResourceNotFoundError(ApiError):
    def __init__(self, message: str = "resource not found", details: dict[str, Any] | None = None) -> None:
        super().__init__("not_found", message, 404, details or {})


class ServiceUnavailableError(ApiError):
    def __init__(
        self,
        code: str = "service_unavailable",
        message: str = "service unavailable",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, 503, details or {})


def error_body(error: ApiError, trace_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        }
    }
    if trace_id:
        body["trace_id"] = trace_id
    return body
