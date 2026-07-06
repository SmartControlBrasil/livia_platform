import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from conversations.models import Conversation, Message
from tenants.models import Tenant
from .services import LiviaDecisionService


@csrf_exempt
@require_POST
def chat_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    tenant_slug = payload.get("tenant") or payload.get("tenant_id")
    session_id = payload.get("session_id")
    user_message = payload.get("message")
    source_page = payload.get("source_page", "")

    if not tenant_slug:
        return JsonResponse({"error": "tenant is required."}, status=400)

    if not session_id:
        return JsonResponse({"error": "session_id is required."}, status=400)

    if not user_message:
        return JsonResponse({"error": "message is required."}, status=400)

    tenant = Tenant.objects.filter(slug=tenant_slug, is_active=True).first()

    if tenant is None:
        return JsonResponse({"error": "Tenant not found or inactive."}, status=404)

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
    )
    Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content=user_message,
    )
    assistant_reply = decision.reply

    Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content=assistant_reply,
    )

    return JsonResponse(
        {
            "tenant": tenant.slug,
            "session_id": session_id,
            "reply": assistant_reply,
            "intent": decision.intent,
        }
    )
