from django.http import JsonResponse

from config.environment_safety import inspect_environment_safety, summarize_environment_readiness


def healthcheck(request):
    if str(request.GET.get("readiness") or "").strip() in {"1", "true", "yes"}:
        checks = inspect_environment_safety()
        status = summarize_environment_readiness(checks)
        return JsonResponse(
            {
                "status": "ok" if status != "NOT_READY" else "not_ready",
                "service": "livia-platform",
                "readiness": status,
                "checks": [
                    {"ok": item.ok, "code": item.code, "detail": item.detail, "level": item.level}
                    for item in checks
                ],
            }
        )
    return JsonResponse({"status": "ok", "service": "livia-platform"})
