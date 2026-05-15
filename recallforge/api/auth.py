"""Authentication and RequestContext injection for the M5 API."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Header, Request

from recallforge.config import Settings
from recallforge.context import RequestContext, current_request_context
from recallforge.storage.models import ACCESS_LEVELS

from .errors import AuthenticationError, PermissionDeniedError

KNOWN_SCOPES = {
    "documents:write",
    "documents:read",
    "knowledge:read",
    "knowledge:answer",
    "knowledge_bases:write",
    "knowledge_bases:read",
    "console:use",
}


@dataclass(frozen=True)
class AuthenticatedRequest:
    context: RequestContext
    scopes: frozenset[str]
    subject_type: str
    key_id: str | None = None


def require_scopes(*required: str):
    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None, alias="Authorization"),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ):
        settings: Settings = request.app.state.settings
        auth = authenticate_request(
            settings,
            authorization=authorization,
            x_api_key=x_api_key,
            x_request_id=x_request_id,
        )
        _ensure_scope(auth.scopes, set(required), match_any=False)
        token = current_request_context.set(auth.context)
        try:
            yield auth
        finally:
            current_request_context.reset(token)

    return dependency


def require_any_scope(*required: str):
    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None, alias="Authorization"),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ):
        settings: Settings = request.app.state.settings
        auth = authenticate_request(
            settings,
            authorization=authorization,
            x_api_key=x_api_key,
            x_request_id=x_request_id,
        )
        _ensure_scope(auth.scopes, set(required), match_any=True)
        token = current_request_context.set(auth.context)
        try:
            yield auth
        finally:
            current_request_context.reset(token)

    return dependency


def authenticate_request(
    settings: Settings,
    *,
    authorization: str | None,
    x_api_key: str | None,
    x_request_id: str | None,
) -> AuthenticatedRequest:
    if not settings.api_require_auth:
        return _dev_auth_context(x_request_id)

    if x_api_key:
        return _authenticate_api_key(settings, x_api_key, x_request_id)

    if authorization:
        scheme, _, value = authorization.partition(" ")
        scheme = scheme.lower()
        if scheme == "bearer" and value:
            return _authenticate_jwt(settings, value, x_request_id)
        if scheme in {"apikey", "api-key"} and value:
            return _authenticate_api_key(settings, value, x_request_id)

    raise AuthenticationError()


def _authenticate_jwt(settings: Settings, token: str, request_id: str | None) -> AuthenticatedRequest:
    if not settings.api_jwt_public_key:
        raise AuthenticationError("JWT verification key is not configured")
    try:
        import jwt

        decode_kwargs: dict[str, Any] = {
            "key": settings.api_jwt_public_key,
            "algorithms": ["HS256", "RS256", "ES256"],
            "options": {
                "verify_aud": bool(settings.api_jwt_audience),
                "verify_iss": bool(settings.api_jwt_issuer),
            },
        }
        if settings.api_jwt_audience:
            decode_kwargs["audience"] = settings.api_jwt_audience
        if settings.api_jwt_issuer:
            decode_kwargs["issuer"] = settings.api_jwt_issuer
        claims = jwt.decode(token, **decode_kwargs)
    except Exception as exc:
        raise AuthenticationError("invalid bearer token", {"error_type": type(exc).__name__}) from exc

    ctx = _context_from_claims(claims, request_id or claims.get("jti"))
    scopes = _normalize_scopes(claims.get("scopes", claims.get("scope", [])))
    return AuthenticatedRequest(context=ctx, scopes=scopes, subject_type="jwt")


def _authenticate_api_key(settings: Settings, api_key: str, request_id: str | None) -> AuthenticatedRequest:
    mapping = _service_key_mapping(settings.api_service_keys)
    record = mapping.get(api_key)
    if record is None:
        raise AuthenticationError("invalid API key")
    ctx = RequestContext(
        tenant_id=_required_string(record, "tenant_id"),
        user_id=_required_string(record, "user_id"),
        department=_required_string(record, "department"),
        access_level=_required_access_level(record),
        request_id=_request_id(request_id),
    )
    scopes = _normalize_scopes(record.get("scopes", []))
    return AuthenticatedRequest(
        context=ctx,
        scopes=scopes,
        subject_type="api_key",
        key_id=record.get("key_id"),
    )


def _service_key_mapping(raw: str) -> dict[str, dict[str, Any]]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthenticationError("api_service_keys is not valid JSON") from exc

    if isinstance(parsed, dict):
        if "keys" in parsed and isinstance(parsed["keys"], list):
            return {
                _required_string(item, "key"): {k: v for k, v in item.items() if k != "key"}
                for item in parsed["keys"]
                if isinstance(item, dict)
            }
        return {
            str(key): value
            for key, value in parsed.items()
            if isinstance(value, dict)
        }
    if isinstance(parsed, list):
        return {
            _required_string(item, "key"): {k: v for k, v in item.items() if k != "key"}
            for item in parsed
            if isinstance(item, dict)
        }
    raise AuthenticationError("api_service_keys must be a JSON object or list")


def _context_from_claims(claims: dict[str, Any], request_id: str | None) -> RequestContext:
    return RequestContext(
        tenant_id=_claim_string(claims, "tenant_id", "tid"),
        user_id=_claim_string(claims, "user_id", "sub"),
        department=_claim_string(claims, "department"),
        access_level=_claim_access_level(claims),
        request_id=_request_id(request_id),
    )


def _claim_string(claims: dict[str, Any], *names: str) -> str:
    for name in names:
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            return value
    raise AuthenticationError(f"missing required claim: {names[0]}")


def _claim_access_level(claims: dict[str, Any]) -> str:
    value = _claim_string(claims, "access_level")
    if value not in ACCESS_LEVELS:
        raise PermissionDeniedError("invalid access_level claim")
    return value


def _required_string(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AuthenticationError(f"api key record missing {name}")
    return value


def _required_access_level(record: dict[str, Any]) -> str:
    value = _required_string(record, "access_level")
    if value not in ACCESS_LEVELS:
        raise PermissionDeniedError("invalid api key access_level")
    return value


def _normalize_scopes(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        items = value.split()
    elif isinstance(value, list | tuple | set):
        items = list(value)
    else:
        items = []
    return frozenset(str(item) for item in items if str(item) in KNOWN_SCOPES)


def _ensure_scope(scopes: frozenset[str], required: set[str], *, match_any: bool) -> None:
    if not required:
        return
    ok = bool(scopes & required) if match_any else required.issubset(scopes)
    if not ok:
        raise PermissionDeniedError(details={"required_scopes": sorted(required)})


def _request_id(value: str | None) -> uuid.UUID:
    if value:
        try:
            return uuid.UUID(str(value))
        except ValueError as exc:
            raise AuthenticationError("request id must be a UUID") from exc
    return uuid.uuid4()


def _dev_auth_context(request_id: str | None) -> AuthenticatedRequest:
    ctx = RequestContext(
        tenant_id="dev-tenant",
        user_id="dev-user",
        department="global",
        access_level="restricted",
        request_id=_request_id(request_id),
    )
    return AuthenticatedRequest(context=ctx, scopes=frozenset(KNOWN_SCOPES), subject_type="dev")
