from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, connection, transaction

from assistant_core.discovery import analyze_message
from assistant_core.services.ai_feature_gates import is_grounded_synthesis_allowed
from conversations.models import Conversation, HandoffRequest, Message
from knowledge_base.rag.context_builder import build_knowledge_context
from tenants.services.human_handoff import build_human_handoff_payload

from .chat_idempotency import complete_chat_request, update_completed_chat_request_response
from .livia_decision import LiviaDecisionService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DeterministicChatResult:
    chat_request: object
    tenant: object
    conversation: Conversation
    assistant_profile: object | None
    history: list[dict]
    user_message: str
    decision: object
    assistant_message: Message
    response_payload: dict


def process_chat_request(*, chat_request, tenant, session_id: str, user_message: str, source_page: str = "") -> dict:
    decision_service = LiviaDecisionService()
    # Recuperação semântica pode chamar provider externo: permanece fora da transação de negócio.
    discovery_preview = analyze_message(user_message)
    knowledge_context = build_knowledge_context(
        tenant,
        user_message,
        service_area=discovery_preview.service_area,
        limit=2,
        conversation=None,
    )
    deterministic_result = _persist_chat_processing_state(
        chat_request=chat_request,
        tenant=tenant,
        session_id=session_id,
        user_message=user_message,
        source_page=source_page,
        decision_service=decision_service,
        knowledge_context=knowledge_context,
    )
    return _refine_response_with_ai_if_enabled(
        deterministic_result=deterministic_result,
        decision_service=decision_service,
        knowledge_context=knowledge_context,
    )


def _persist_chat_processing_state(
    *,
    chat_request,
    tenant,
    session_id: str,
    user_message: str,
    source_page: str,
    decision_service: LiviaDecisionService,
    knowledge_context: str = "",
) -> _DeterministicChatResult:
    with transaction.atomic():
        conversation = _get_or_create_locked_conversation(tenant=tenant, session_id=session_id, source_page=source_page)
        assistant_profile = _active_assistant_profile(tenant)
        history = list(conversation.messages.values("role", "content").order_by("created_at", "id"))

        # Mantém decisão determinística no bloco atômico e adia IA externa para fora da transação.
        decision = decision_service.generate_reply(
            history=history,
            current_message=user_message,
            conversation=conversation,
            assistant_profile=_assistant_profile_without_ai(assistant_profile),
            knowledge_context=knowledge_context,
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

        assistant_message = Message.objects.create(
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
        complete_chat_request(chat_request, response_payload=response_payload, status_code=200, conversation=conversation)
        return _DeterministicChatResult(
            chat_request=chat_request,
            tenant=tenant,
            conversation=conversation,
            assistant_profile=assistant_profile,
            history=history,
            user_message=user_message,
            decision=decision,
            assistant_message=assistant_message,
            response_payload=response_payload,
        )


def _refine_response_with_ai_if_enabled(
    *,
    deterministic_result: _DeterministicChatResult,
    decision_service: LiviaDecisionService,
    knowledge_context: str = "",
) -> dict:
    if not _can_refine_with_ai(deterministic_result.assistant_profile):
        return deterministic_result.response_payload
    if deterministic_result.response_payload.get("human_handoff", {}).get("active"):
        return deterministic_result.response_payload

    discovery = analyze_message(deterministic_result.user_message)
    context = knowledge_context or build_knowledge_context(
        deterministic_result.tenant,
        deterministic_result.user_message,
        service_area=discovery.service_area,
        limit=2,
        conversation=deterministic_result.conversation,
    )

    from assistant_core.services.grounded_response import GroundedResponseService

    grounded_service = GroundedResponseService(ai_client=decision_service.ai_client)
    try:
        grounded = grounded_service.generate(
            tenant=deterministic_result.tenant,
            assistant_profile=deterministic_result.assistant_profile,
            message=deterministic_result.user_message,
            conversation=deterministic_result.conversation,
            discovery=discovery,
            decision=deterministic_result.decision,
            knowledge_context=context,
            history=deterministic_result.history,
        )
    except Exception:
        logger.exception(
            "ai.grounded.failed tenant_slug=%s session_hash_unavailable",
            deterministic_result.tenant.slug,
        )
        grounded = None

    if grounded is not None and grounded.used and grounded.text:
        return _apply_refined_reply(deterministic_result, grounded.text, ai_mode="grounded")

    if is_grounded_synthesis_allowed(
        tenant_slug=deterministic_result.tenant.slug,
        assistant_profile=deterministic_result.assistant_profile,
    ):
        # Tenant configurado para grounded: não cair no rewrite legado.
        return deterministic_result.response_payload

    try:
        refined_decision = decision_service._finalize_ai_response(  # noqa: SLF001
            decision=deterministic_result.decision,
            conversation=deterministic_result.conversation,
            assistant_profile=deterministic_result.assistant_profile,
            discovery=discovery,
            current_message=deterministic_result.user_message,
            history=deterministic_result.history,
            knowledge_context=context,
        )
    except Exception:
        logger.exception(
            "livia_ai_post_commit_refine_failed tenant_slug=%s session_hash_unavailable",
            deterministic_result.tenant.slug,
        )
        return deterministic_result.response_payload

    if refined_decision.reply == deterministic_result.response_payload.get("reply", ""):
        return deterministic_result.response_payload

    return _apply_refined_reply(deterministic_result, refined_decision.reply, ai_mode="rewrite")


def _apply_refined_reply(deterministic_result: _DeterministicChatResult, reply: str, *, ai_mode: str) -> dict:
    updated_payload = dict(deterministic_result.response_payload)
    updated_payload["reply"] = reply
    updated_payload["ai_mode"] = ai_mode
    with transaction.atomic():
        Message.objects.filter(pk=deterministic_result.assistant_message.pk).update(content=reply)
        update_completed_chat_request_response(
            deterministic_result.chat_request,
            response_payload=updated_payload,
            status_code=200,
        )
    return updated_payload


def _active_assistant_profile(tenant):
    try:
        assistant_profile = tenant.assistant_profile
    except ObjectDoesNotExist:
        return None
    if assistant_profile is not None and not assistant_profile.is_active:
        return None
    return assistant_profile


def _assistant_profile_without_ai(assistant_profile):
    if assistant_profile is None or not getattr(assistant_profile, "use_ai", False):
        return assistant_profile
    return SimpleNamespace(
        name=getattr(assistant_profile, "name", ""),
        initial_message=getattr(assistant_profile, "initial_message", ""),
        tone=getattr(assistant_profile, "tone", ""),
        primary_goal=getattr(assistant_profile, "primary_goal", ""),
        business_name=getattr(assistant_profile, "business_name", ""),
        business_domain=getattr(assistant_profile, "business_domain", ""),
        short_description=getattr(assistant_profile, "short_description", ""),
        use_ai=False,
    )


def _can_refine_with_ai(assistant_profile) -> bool:
    if assistant_profile is None:
        return False
    if bool(getattr(settings, "RUNNING_TESTS", False)):
        return False
    return bool(getattr(settings, "LIVIA_AI_ENABLED", False)) and bool(getattr(assistant_profile, "use_ai", False))


def _get_or_create_locked_conversation(*, tenant, session_id: str, source_page: str = "") -> Conversation:
    queryset = Conversation.objects.filter(tenant=tenant, session_id=session_id)
    if connection.features.has_select_for_update:
        queryset = queryset.select_for_update()
    conversation = queryset.first()
    if conversation is None:
        try:
            conversation = Conversation.objects.create(
                tenant=tenant,
                session_id=session_id,
                source_page=source_page,
            )
        except IntegrityError:
            queryset = Conversation.objects.filter(tenant=tenant, session_id=session_id)
            if connection.features.has_select_for_update:
                queryset = queryset.select_for_update()
            conversation = queryset.get()
    elif source_page and not conversation.source_page:
        conversation.source_page = source_page
        conversation.save(update_fields=["source_page", "updated_at"])
    return conversation
