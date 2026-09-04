from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from types import SimpleNamespace

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, connection, transaction

from assistant_core.discovery import analyze_message
from assistant_core.prompts.livia import DEFAULT_REPLY
from assistant_core.services.ai_feature_gates import is_grounded_synthesis_allowed
from assistant_core.services.deterministic_synthesis import is_generic_fallback_reply
from conversations.models import Conversation, HandoffRequest, Message
from knowledge_base.rag.context_builder import KnowledgeContextResult, build_knowledge_context, build_knowledge_context_result
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
    from assistant_core.consultative_policy import detect_collection_trigger, CollectionTrigger
    from assistant_core.dialogue_memory import (
        build_contextual_retrieval_query,
        load_dialogue_memory,
        persist_dialogue_memory,
        update_dialogue_memory_from_turn,
    )
    from assistant_core.services.response_quality_gate import apply_response_quality_gate

    decision_service = LiviaDecisionService()
    # Garante conversation_id em RagRetrievalEvent sem alterar a política de resposta.
    conversation_ref = _ensure_conversation_for_retrieval(tenant=tenant, session_id=session_id, source_page=source_page)
    history_preview = list(conversation_ref.messages.values("role", "content").order_by("created_at", "id"))
    memory = load_dialogue_memory(conversation_ref)
    discovery_preview = analyze_message(user_message)
    commercial = detect_collection_trigger(user_message) != CollectionTrigger.NONE
    memory = update_dialogue_memory_from_turn(
        memory=memory,
        current_message=user_message,
        history=history_preview,
        need_summary=memory.active_need,
        commercial_trigger=commercial,
        tenant=tenant,
    )
    original_query, contextual_query = build_contextual_retrieval_query(
        current_message=user_message,
        memory=memory,
        history=history_preview,
        need_summary=memory.active_need,
    )
    memory.retrieval_query_original = original_query
    memory.retrieval_query_contextual = contextual_query

    # Recuperação semântica pode chamar provider externo: permanece fora da transação de negócio.
    knowledge_result = build_knowledge_context_result(
        tenant,
        user_message,
        service_area=discovery_preview.service_area,
        limit=2,
        conversation=conversation_ref,
        contextual_query=contextual_query,
        active_domain=memory.active_domain,
        active_entity=memory.active_entity,
        active_application=memory.active_application,
        active_subject=memory.active_knowledge_subject,
        retrieval_query_original=original_query,
    )
    knowledge_context = knowledge_result.text
    memory.entity_match = bool(knowledge_result.entity_match or memory.entity_match)
    memory.domain_match = bool(knowledge_result.domain_match or memory.domain_match)

    deterministic_result = _persist_chat_processing_state(
        chat_request=chat_request,
        tenant=tenant,
        session_id=session_id,
        user_message=user_message,
        source_page=source_page,
        decision_service=decision_service,
        knowledge_context=knowledge_context,
        knowledge_result=knowledge_result,
        dialogue_memory=memory,
    )
    return _refine_response_with_ai_if_enabled(
        deterministic_result=deterministic_result,
        decision_service=decision_service,
        knowledge_context=knowledge_context,
    )


def _ensure_conversation_for_retrieval(*, tenant, session_id: str, source_page: str) -> Conversation:
    existing = Conversation.objects.filter(tenant=tenant, session_id=session_id).first()
    if existing is not None:
        return existing
    try:
        return Conversation.objects.create(tenant=tenant, session_id=session_id, source_page=source_page or "")
    except IntegrityError:
        return Conversation.objects.get(tenant=tenant, session_id=session_id)


def _persist_chat_processing_state(
    *,
    chat_request,
    tenant,
    session_id: str,
    user_message: str,
    source_page: str,
    decision_service: LiviaDecisionService,
    knowledge_context: str = "",
    knowledge_result: KnowledgeContextResult | None = None,
    dialogue_memory=None,
) -> _DeterministicChatResult:
    from assistant_core.dialogue_memory import persist_dialogue_memory, should_skip_consultative_followup
    from assistant_core.services.response_quality_gate import apply_response_quality_gate

    with transaction.atomic():
        conversation = _get_or_create_locked_conversation(tenant=tenant, session_id=session_id, source_page=source_page)
        assistant_profile = _active_assistant_profile(tenant)
        history = list(conversation.messages.values("role", "content").order_by("created_at", "id"))

        # Mantém decisão determinística no bloco atômico e adia IA externa para fora da transação.
        decision_started = time.monotonic()
        decision = decision_service.generate_reply(
            history=history,
            current_message=user_message,
            conversation=conversation,
            assistant_profile=_assistant_profile_without_ai(assistant_profile),
            knowledge_context=knowledge_context,
        )
        decision_ms = int((time.monotonic() - decision_started) * 1000)
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content=user_message,
        )

        assistant_reply = str(decision.reply or "").strip()
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

        gate_diagnostics = {}
        if dialogue_memory is not None and human_handoff_payload is None:
            try:
                lead = conversation.lead_draft
            except Exception:
                lead = None
            collection_active = bool(
                lead is not None
                and isinstance(getattr(lead, "qualification_data", None), dict)
                and (lead.qualification_data or {}).get("collection_active")
            )
            # Não reescrever prompts de coleta (nome/telefone) com síntese RAG.
            if not collection_active:
                assistant_reply, gate_diagnostics = apply_response_quality_gate(
                    reply=assistant_reply,
                    knowledge_context=knowledge_context,
                    current_message=user_message,
                    memory=dialogue_memory,
                    history=history,
                    append_followup=False if should_skip_consultative_followup(current_message=user_message, memory=dialogue_memory) else None,
                )
            if lead is not None:
                persist_dialogue_memory(lead, dialogue_memory)

        assistant_reply = str(assistant_reply or "").strip()
        if not assistant_reply:
            # Fail-closed: sucesso HTTP nunca devolve reply vazia.
            assistant_reply = DEFAULT_REPLY
            logger.warning(
                "livia_empty_reply_replaced tenant_slug=%s session_id=%s",
                getattr(tenant, "slug", ""),
                session_id,
            )

        assistant_message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content=assistant_reply,
        )

        observability = {
            "is_fallback": bool(is_generic_fallback_reply(assistant_reply)),
            "intent": getattr(decision, "intent", "") or "",
            "retrieval_attempted": bool(knowledge_result and knowledge_result.mode in {"semantic", "keyword"}),
            "retrieval_status": getattr(knowledge_result, "retrieval_status", "") or "",
            "retrieval_hit": bool(getattr(knowledge_result, "retrieval_hit", False)),
            "max_score": float(getattr(knowledge_result, "max_score", 0.0) or 0.0),
            "chunk_count": int(getattr(knowledge_result, "result_count", 0) or 0),
            "retrieval_ms": int(getattr(knowledge_result, "duration_ms", 0) or 0),
            "decision_ms": decision_ms,
            "retrieval_mode": getattr(knowledge_result, "mode", "") or "",
            "policy_chunks_filtered": int(getattr(knowledge_result, "policy_chunks_filtered", 0) or 0),
            "coherence_filtered_count": int(
                getattr(knowledge_result, "coherence_filtered_count", 0) or gate_diagnostics.get("coherence_filtered_count", 0) or 0
            ),
            "collection_trigger_reason": "",
            "followup_strategy": gate_diagnostics.get("followup_strategy", ""),
            "policy_leak_blocked": bool(gate_diagnostics.get("policy_leak_blocked")),
            "policy_chunk_selected": 0,
            "active_subject": getattr(dialogue_memory, "active_knowledge_subject", {}) if dialogue_memory is not None else {},
            "contextual_query_used": bool(
                dialogue_memory
                and dialogue_memory.retrieval_query_contextual
                and dialogue_memory.retrieval_query_contextual != dialogue_memory.retrieval_query_original
            ),
        }
        if dialogue_memory is not None:
            observability.update(dialogue_memory.observability())
            observability["entity_match"] = bool(dialogue_memory.entity_match or getattr(knowledge_result, "entity_match", False))
            observability["domain_match"] = bool(dialogue_memory.domain_match or getattr(knowledge_result, "domain_match", False))
            observability["contextual_query_used"] = bool(
                dialogue_memory.retrieval_query_contextual
                and dialogue_memory.retrieval_query_contextual.strip()
                and dialogue_memory.retrieval_query_contextual.strip()
                != (dialogue_memory.retrieval_query_original or "").strip()
            )
        # Motivo de coleta quando disponível no lead.
        try:
            lead_obs = conversation.lead_draft
            qd = dict(getattr(lead_obs, "qualification_data", None) or {})
            if qd.get("collection_active"):
                observability["collection_trigger_reason"] = qd.get("collection_trigger_reason") or "collection_active"
            elif qd.get("contact_collection_deferred"):
                observability["collection_trigger_reason"] = "contact_deferred"
        except Exception:
            pass
        # Observabilidade na resposta/API e no ChatRequest deve ser idêntica (idempotência).
        # Queries completas ficam no RagRetrievalEvent; aqui só flags e domínio/entidade.
        client_observability = {
            key: value
            for key, value in observability.items()
            if key not in {"retrieval_query_original", "retrieval_query_contextual"}
        }
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
            "observability": client_observability,
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
