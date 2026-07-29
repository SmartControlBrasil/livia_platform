import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from tenants.models import Tenant
from tenants.origins import log_origin_block, validate_tenant_origin

from .security.ip import get_client_ip
from .security.rate_limit import check_chat_rate_limit
from .security.spam_guard import check_message_spam
from .services.chat_idempotency import (
    REQUEST_ID_HEADER,
    REQUEST_ID_CONFLICT,
    REQUEST_IN_PROGRESS,
    ChatIdempotencyError,
    build_request_fingerprint,
    complete_chat_request,
    fail_chat_request,
    parse_request_id,
    reserve_chat_request,
)
from .services.chat_processing import process_chat_request

logger = logging.getLogger(__name__)

INACTIVE_TENANT_REPLY = "Este atendimento não está disponível no momento."
LONG_MESSAGE_REPLY = "Sua mensagem está muito longa. Envie uma versão mais curta para eu conseguir te ajudar melhor."
RATE_LIMIT_REPLY = "Recebi muitas mensagens em pouco tempo. Aguarde alguns minutos e tente novamente."
SPAM_REPLY = "Não consegui processar essa mensagem. Envie uma solicitação objetiva sobre atendimento, orçamento ou suporte."
EMPTY_MESSAGE_ERROR = "message is required."
REQUEST_IN_PROGRESS_REPLY = "Sua mensagem ainda está sendo processada. Tente novamente em instantes."
REQUEST_FAILED_REPLY = "Não consegui concluir essa mensagem. Tente enviar novamente em instantes."
UNEXPECTED_ERROR_REPLY = "Não consegui responder agora. Tente novamente em instantes."


def _safe_reply_response(reply, *, status=200, code="blocked"):
    return JsonResponse({"error": code, "reply": reply}, status=status)


def _json_response(payload, *, status=200, replay=False):
    response = JsonResponse(payload, status=status)
    response["X-Livia-Idempotent-Replay"] = "true" if replay else "false"
    return response


@csrf_exempt
def chat_api(request):
    if request.method == "OPTIONS":
        return JsonResponse({}, status=204)

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    tenant_slug = payload.get("tenant") or payload.get("tenant_id")
    header_tenant_slug = request.headers.get("X-Livia-Tenant", "").strip()
    session_id = payload.get("session_key") or payload.get("session_id")
    user_message_raw = payload.get("message")
    source_page = payload.get("source_page", "")

    if not tenant_slug:
        return JsonResponse({"error": "tenant is required."}, status=400)
    tenant_slug = str(tenant_slug).strip()
    if header_tenant_slug and header_tenant_slug != tenant_slug:
        return JsonResponse({"error": "tenant header mismatch."}, status=400)

    tenant = Tenant.objects.filter(slug=tenant_slug).first()
    if tenant is None:
        return _safe_reply_response(INACTIVE_TENANT_REPLY, status=404, code="tenant_unavailable")
    if not tenant.is_active:
        logger.info("livia_chat_inactive_tenant tenant_slug=%s", tenant_slug)
        return _safe_reply_response(INACTIVE_TENANT_REPLY, status=403, code="tenant_unavailable")

    origin_result = validate_tenant_origin(request, tenant)
    if not origin_result.allowed:
        log_origin_block(tenant, origin_result)
        return JsonResponse({"error": "origin_not_allowed"}, status=403)
    request.livia_validated_origin = origin_result.origin

    if not session_id:
        return JsonResponse({"error": "session_id is required."}, status=400)
    session_id = str(session_id).strip()

    if not isinstance(user_message_raw, str):
        return JsonResponse({"error": EMPTY_MESSAGE_ERROR}, status=400)

    user_message = user_message_raw.strip()
    if not user_message:
        return JsonResponse({"error": EMPTY_MESSAGE_ERROR}, status=400)

    try:
        request_id = parse_request_id(payload.get("request_id"), request.headers.get(REQUEST_ID_HEADER, ""))
    except ChatIdempotencyError as exc:
        return JsonResponse({"error": exc.code, "message": exc.message}, status=400)

    max_message_length = int(getattr(settings, "LIVIA_MAX_MESSAGE_LENGTH", 1200))
    if len(user_message) > max_message_length:
        logger.info(
            "livia_chat_message_too_long tenant_slug=%s message_length=%s max_length=%s",
            tenant_slug,
            len(user_message),
            max_message_length,
        )
        return _safe_reply_response(LONG_MESSAGE_REPLY, status=400, code="message_too_long")

    fingerprint = build_request_fingerprint(
        tenant_slug=tenant.slug,
        session_id=session_id,
        request_id=request_id,
        message=user_message,
        source_page=source_page,
    )
    reservation = reserve_chat_request(
        tenant=tenant,
        session_id=session_id,
        request_id=request_id,
        fingerprint=fingerprint,
    )

    if reservation.state == "replay":
        return _json_response(
            reservation.response_payload or {},
            status=reservation.response_status_code,
            replay=True,
        )
    if reservation.state == "conflict":
        return _json_response({"error": REQUEST_ID_CONFLICT}, status=409, replay=False)
    if reservation.state == "in_progress":
        return _json_response(
            {"error": REQUEST_IN_PROGRESS, "reply": REQUEST_IN_PROGRESS_REPLY},
            status=409,
            replay=False,
        )
    if reservation.state == "failed":
        return _json_response(
            {"error": reservation.error_code or "request_failed", "reply": REQUEST_FAILED_REPLY},
            status=409,
            replay=False,
        )

    chat_request = reservation.chat_request
    client_ip = get_client_ip(request)
    try:
        rate_limit = check_chat_rate_limit(tenant.slug, client_ip)
        if not rate_limit.allowed:
            logger.info(
                "livia_chat_rate_limited tenant_slug=%s ip=%s limit=%s window_seconds=%s",
                tenant.slug,
                client_ip,
                rate_limit.limit,
                rate_limit.window_seconds,
            )
            response_payload = {"error": "rate_limited", "reply": RATE_LIMIT_REPLY}
            complete_chat_request(chat_request, response_payload=response_payload, status_code=429)
            return _json_response(response_payload, status=429, replay=False)

        spam_check = check_message_spam(user_message)
        if spam_check.is_spam:
            logger.info(
                "livia_chat_spam_blocked tenant_slug=%s ip=%s reason=%s message_length=%s",
                tenant.slug,
                client_ip,
                spam_check.reason,
                len(user_message),
            )
            response_payload = {"error": "spam_blocked", "reply": SPAM_REPLY}
            complete_chat_request(chat_request, response_payload=response_payload, status_code=400)
            return _json_response(response_payload, status=400, replay=False)

        response_payload = process_chat_request(
            chat_request=chat_request,
            tenant=tenant,
            session_id=session_id,
            user_message=user_message,
            source_page=source_page,
        )
        return _json_response(response_payload, status=200, replay=False)
    except Exception:
        fail_chat_request(chat_request, error_code="unexpected_error")
        logger.exception(
            "livia_chat_unexpected_error tenant_slug=%s session_hash_unavailable request_id=%s",
            tenant.slug,
            request_id,
        )
        return _json_response({"error": "unexpected_error", "reply": UNEXPECTED_ERROR_REPLY}, status=500, replay=False)
