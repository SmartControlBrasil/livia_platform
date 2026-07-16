import logging
from urllib.parse import urlparse

from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers

logger = logging.getLogger(__name__)


class LiviaWidgetCorsMiddleware:
    CORS_PATHS = {"/api/chat/", "/api/widget/config/"}
    ALLOWED_HEADERS = "Content-Type, Authorization"
    ALLOWED_METHODS = "GET, POST, OPTIONS"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in self.CORS_PATHS and request.method == "OPTIONS":
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        if request.path in self.CORS_PATHS:
            self._patch_cors_headers(request, response)
        return response

    def _patch_cors_headers(self, request, response):
        origin = request.headers.get("Origin")
        if not origin:
            return

        if not self._is_allowed_origin(origin):
            logger.info("livia_chat_origin_blocked origin_host=%s", urlparse(origin).hostname or "unknown")
            return

        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Methods"] = self.ALLOWED_METHODS
        response["Access-Control-Allow-Headers"] = self.ALLOWED_HEADERS
        response["Access-Control-Max-Age"] = "86400"
        patch_vary_headers(response, ("Origin",))

    def _is_allowed_origin(self, origin: str) -> bool:
        allowed_origins = set(getattr(settings, "LIVIA_ALLOWED_WIDGET_ORIGINS", []))
        if not allowed_origins:
            return True
        if origin in allowed_origins:
            return True
        if getattr(settings, "DEBUG", False):
            hostname = urlparse(origin).hostname or ""
            return hostname in {"localhost", "127.0.0.1"}
        return False
