import json
import logging

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from conversations.models import Conversation, HandoffRequest, Message
from tenants.models import Tenant
from tenants.services.human_handoff import build_human_handoff_payload
from .security.ip import get_client_ip
from .security.rate_limit import check_chat_rate_limit
from .security.spam_guard import check_message_spam
from .services import LiviaDecisionService

logger = logging.getLogger(__name__)

INACTIVE_TENANT_REPLY = "Este atendimento não está disponível no momento."
LONG_MESSAGE_REPLY = "Sua mensagem está muito longa. Envie uma versão mais curta para eu conseguir te ajudar melhor."
RATE_LIMIT_REPLY = "Recebi muitas mensagens em pouco tempo. Aguarde alguns minutos e tente novamente."
SPAM_REPLY = "Não consegui processar essa mensagem. Envie uma solicitação objetiva sobre atendimento, orçamento ou suporte."
EMPTY_MESSAGE_ERROR = "message is required."


def _safe_reply_response(reply, *, status=200, code="blocked"):
    return JsonResponse({"error": code, "reply": reply}, status=status)


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
    session_id = payload.get("session_key") or payload.get("session_id")
    user_message_raw = payload.get("message")
    source_page = payload.get("source_page", "")

    if not tenant_slug:
        return JsonResponse({"error": "tenant is required."}, status=400)

    if not session_id:
        return JsonResponse({"error": "session_id is required."}, status=400)

    if not isinstance(user_message_raw, str):
        return JsonResponse({"error": EMPTY_MESSAGE_ERROR}, status=400)

    user_message = user_message_raw.strip()
    if not user_message:
        return JsonResponse({"error": EMPTY_MESSAGE_ERROR}, status=400)

    max_message_length = int(getattr(settings, "LIVIA_MAX_MESSAGE_LENGTH", 1200))
    if len(user_message) > max_message_length:
        logger.info(
            "livia_chat_message_too_long tenant_slug=%s message_length=%s max_length=%s",
            tenant_slug,
            len(user_message),
            max_message_length,
        )
        return _safe_reply_response(LONG_MESSAGE_REPLY, status=400, code="message_too_long")

    tenant = Tenant.objects.filter(slug=tenant_slug).first()

    if tenant is None:
        return _safe_reply_response(INACTIVE_TENANT_REPLY, status=404, code="tenant_unavailable")

    if not tenant.is_active:
        logger.info("livia_chat_inactive_tenant tenant_slug=%s", tenant_slug)
        return _safe_reply_response(INACTIVE_TENANT_REPLY, status=403, code="tenant_unavailable")

    client_ip = get_client_ip(request)
    rate_limit = check_chat_rate_limit(tenant.slug, client_ip)
    if not rate_limit.allowed:
        logger.info(
            "livia_chat_rate_limited tenant_slug=%s ip=%s limit=%s window_seconds=%s",
            tenant.slug,
            client_ip,
            rate_limit.limit,
            rate_limit.window_seconds,
        )
        return _safe_reply_response(RATE_LIMIT_REPLY, status=429, code="rate_limited")

    spam_check = check_message_spam(user_message)
    if spam_check.is_spam:
        logger.info(
            "livia_chat_spam_blocked tenant_slug=%s ip=%s reason=%s message_length=%s",
            tenant.slug,
            client_ip,
            spam_check.reason,
            len(user_message),
        )
        return _safe_reply_response(SPAM_REPLY, status=400, code="spam_blocked")

    try:
        assistant_profile = tenant.assistant_profile
    except ObjectDoesNotExist:
        assistant_profile = None
    if assistant_profile is not None and not assistant_profile.is_active:
        assistant_profile = None

    conversation, _ = Conversation.objects.get_or_create(
        tenant=tenant,
        session_id=session_id,
        defaults={"source_page": source_page},
    )

    history = list(conversation.messages.values("role", "content").order_by("created_at", "id"))
    decision_service = LiviaDecisionService()
    decision = decision_service.generate_reply(
        history=history,
        current_message=user_message,
        conversation=conversation,
        assistant_profile=assistant_profile,
    )
    Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content=user_message,
    )
    assistant_reply = decision.reply
    human_handoff_payload = None
    if decision.handoff_request_id and decision.handoff_reason == HandoffRequest.Reason.EXPLICIT_REQUEST:
        handoff = HandoffRequest.objects.filter(
            pk=decision.handoff_request_id,
            tenant=tenant,
            conversation=conversation,
        ).first()
        human_handoff_payload = build_human_handoff_payload(assistant_profile, handoff)
        if human_handoff_payload.get("active"):
            assistant_reply = "Claro. Use o botão do WhatsApp que apareceu na tela para falar com nossa equipe."

    Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content=assistant_reply,
    )

    response_payload = {
        "tenant": tenant.slug,
        "session_id": session_id,
        "session_key": session_id,
        "reply": assistant_reply,
        "intent": decision.intent,
        "assistant_name": getattr(assistant_profile, "name", "Lívia"),
        "initial_message": getattr(
            assistant_profile,
            "initial_message",
            "Olá! Sou a Lívia. Como posso te ajudar?",
        ),
    }
    if human_handoff_payload is not None:
        response_payload["human_handoff"] = human_handoff_payload
    return JsonResponse(response_payload)
