import hashlib
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    count: int
    limit: int
    window_seconds: int


def _cache_key(tenant_slug, ip_address, window_bucket):
    raw_key = f"{tenant_slug}:{ip_address}:{window_bucket}".encode("utf-8")
    digest = hashlib.sha256(raw_key).hexdigest()
    return f"livia-chat-rate:{digest}"


def check_chat_rate_limit(tenant_slug, ip_address):
    limit = int(getattr(settings, "LIVIA_CHAT_RATE_LIMIT_REQUESTS", 20))
    window_seconds = int(getattr(settings, "LIVIA_CHAT_RATE_LIMIT_WINDOW_SECONDS", 300))
    if not getattr(settings, "LIVIA_CHAT_RATE_LIMIT_ENABLED", True):
        return RateLimitResult(True, 0, limit, window_seconds)
    if limit <= 0 or window_seconds <= 0:
        return RateLimitResult(True, 0, limit, window_seconds)

    window_bucket = int(time.time() // window_seconds)
    key = _cache_key(tenant_slug, ip_address, window_bucket)
    try:
        count = cache.get(key, 0)
        if count >= limit:
            return RateLimitResult(False, count, limit, window_seconds)
        cache.set(key, count + 1, timeout=window_seconds)
    except Exception:
        return RateLimitResult(True, 0, limit, window_seconds)
    return RateLimitResult(True, count + 1, limit, window_seconds)
