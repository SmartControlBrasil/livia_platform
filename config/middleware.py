from django.http import HttpResponse

from tenants.origins import (
    log_origin_block,
    resolve_public_tenant,
    patch_public_cors_headers,
    validate_tenant_origin,
)


class LiviaWidgetCorsMiddleware:
    CORS_PATHS = {"/api/chat/", "/api/widget/config/"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in self.CORS_PATHS and request.method == "OPTIONS":
            response = self._handle_preflight(request)
        else:
            response = self.get_response(request)

        cors_origin = getattr(request, "livia_validated_origin", "")
        if request.path in self.CORS_PATHS and cors_origin:
            patch_public_cors_headers(response, cors_origin)
        return response

    def _handle_preflight(self, request):
        resolution = resolve_public_tenant(request)
        if resolution.error == "tenant_mismatch":
            return HttpResponse(status=400)
        if resolution.error:
            return HttpResponse(status=403)

        result = validate_tenant_origin(request, resolution.tenant)
        if not result.allowed:
            log_origin_block(resolution.tenant, result)
            return HttpResponse(status=403)
        request.livia_validated_origin = result.origin
        response = HttpResponse(status=204)
        patch_public_cors_headers(response, result.origin)
        return response
