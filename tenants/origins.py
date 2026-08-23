from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.cache import patch_vary_headers

logger = logging.getLogger(__name__)

PUBLIC_TENANT_HEADER = "X-Livia-Tenant"
PUBLIC_TENANT_META = "HTTP_X_LIVIA_TENANT"
PUBLIC_REQUEST_ID_HEADER = "X-Livia-Request-ID"
ALLOWED_CORS_HEADERS = f"Content-Type, {PUBLIC_TENANT_HEADER}, {PUBLIC_REQUEST_ID_HEADER}"
ALLOWED_CORS_METHODS = "GET, POST, OPTIONS"
DEV_LOCAL_ORIGINS = {
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
}


@dataclass(frozen=True)
class OriginValidationResult:
    allowed: bool
    origin: str = ""
    reason: str = ""


def normalize_origin(value):
    raw_value = str(value or "").strip()
    if not raw_value:
        raise ValidationError("Origin is required.")
    if raw_value == "*":
        raise ValidationError("Wildcard origin is not allowed.")

    parsed = urlparse(raw_value)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValidationError("Origin must use http or https.")
    if not parsed.netloc or not parsed.hostname:
        raise ValidationError("Origin must include a host.")
    if parsed.username or parsed.password:
        raise ValidationError("Origin must not include credentials.")
    if parsed.path not in {"", "/"}:
        raise ValidationError("Origin must not include a path.")
    if parsed.query:
        raise ValidationError("Origin must not include a query string.")
    if parsed.fragment:
        raise ValidationError("Origin must not include a fragment.")

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{hostname}:{parsed.port}"
    return f"{scheme}://{netloc}"


def get_request_origin(request):
    return request.headers.get("Origin", "").strip()


def get_public_tenant_slug(request, *, payload=None):
    header_slug = request.headers.get(PUBLIC_TENANT_HEADER, "").strip()
    query_slug = request.GET.get("tenant", "").strip()
    payload_slug = ""
    if payload is not None:
        payload_slug = str(payload.get("tenant") or payload.get("tenant_id") or "").strip()
    return header_slug or payload_slug or query_slug


def get_tenant_from_public_request(request, *, payload=None):
    from tenants.models import Tenant

    tenant_slug = get_public_tenant_slug(request, payload=payload)
    if not tenant_slug:
        return None
    return Tenant.objects.filter(slug=tenant_slug).first()


def is_origin_allowed(tenant, origin):
    result = validate_origin_for_tenant(tenant, origin)
    return result.allowed


def validate_tenant_origin(request, tenant):
    return validate_origin_for_tenant(tenant, get_request_origin(request))


def validate_origin_for_tenant(tenant, origin):
    if tenant is None:
        return OriginValidationResult(False, reason="tenant_missing")
    if not tenant.is_active:
        return OriginValidationResult(False, reason="tenant_inactive")

    if not origin:
        if _allow_originless_public_api():
            return OriginValidationResult(True, origin="", reason="originless_allowed")
        return OriginValidationResult(False, reason="origin_missing")

    try:
        normalized = normalize_origin(origin)
    except ValidationError:
        return OriginValidationResult(False, reason="origin_malformed")

    if _is_dev_local_origin_allowed(normalized):
        return OriginValidationResult(True, origin=normalized, reason="dev_local_allowed")

    exists = tenant.allowed_origins.filter(origin=normalized, is_active=True).exists()
    if exists:
        return OriginValidationResult(True, origin=normalized)

    if not tenant.allowed_origins.filter(is_active=True).exists():
        return OriginValidationResult(False, origin=normalized, reason="no_active_origins")
    return OriginValidationResult(False, origin=normalized, reason="origin_not_allowed")


def log_origin_block(tenant, result):
    tenant_slug = getattr(tenant, "slug", "") or "unknown"
    host = "unknown"
    if result.origin:
        host = urlparse(result.origin).hostname or "unknown"
    logger.info("livia_public_origin_blocked tenant_slug=%s origin_host=%s reason=%s", tenant_slug, host, result.reason)


def patch_public_cors_headers(response, origin):
    if not origin:
        return response
    response["Access-Control-Allow-Origin"] = origin
    response["Access-Control-Allow-Methods"] = ALLOWED_CORS_METHODS
    response["Access-Control-Allow-Headers"] = ALLOWED_CORS_HEADERS
    response["Access-Control-Max-Age"] = "86400"
    patch_vary_headers(response, ("Origin",))
    return response


def _allow_originless_public_api():
    return bool(getattr(settings, "LIVIA_ALLOW_ORIGINLESS_PUBLIC_API", False))


def _is_dev_local_origin_allowed(origin):
    if not getattr(settings, "DEBUG", False):
        return False
    configured = set(getattr(settings, "LIVIA_DEV_ALLOWED_WIDGET_ORIGINS", []) or [])
    allowed = configured or DEV_LOCAL_ORIGINS
    return origin in allowed
