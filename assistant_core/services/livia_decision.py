from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Iterable

from django.conf import settings

from assistant_core.conversation_turns import (
    TurnKind,
    build_direct_question_reply,
    build_enrichment_reply,
    build_name_deferred_reply,
    classify_conversation_turn,
    mark_name_deferred,
    normalize_text,
)
from assistant_core.consultative_policy import (
    CollectionTrigger,
    build_consultative_commercial_reply,
    build_conceptual_price_reply,
    decide_collection,
    is_conceptual_price_question,
    mark_collection_active,
)
from assistant_core.discovery import analyze_message
from assistant_core.discovery.contextual import resolve_discovery_question, should_ask_profile_discovery
from assistant_core.services.decision_outcome import has_semantic_knowledge_block, should_combine_kb_with_discovery, is_informational_knowledge_query
from assistant_core.prompts import (
    DEFAULT_REPLY,
    build_contextual_reply,
)
from assistant_core.services.deterministic_synthesis import (
    is_generic_fallback_reply,
    prefer_contextual_reply_over_fallback,
    synthesize_deterministic_reply,
)
from assistant_core.prompts.livia_ai import build_livia_ai_prompt
from assistant_core.qualification import has_basic_contact
from assistant_core.state import LeadState, can_start_new_cycle, set_state, should_lock_lead
from leads.services import CRMDispatchService, LeadCaptureService
from leads.services.handoff import HandoffService
from integrations.openai.client import OpenAIChatClient
from knowledge_base.rag.context_builder import build_knowledge_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiviaReply:
    intent: str
    reply: str
    handoff_request_id: int | None = None
    handoff_reason: str = ""


@dataclass(frozen=True)
class AssistantProfileContext:
    name: str = "Lívia"
    initial_message: str = "Olá! Sou a Lívia. Como posso te ajudar?"
    tone: str = "consultivo, claro e profissional"
    primary_goal: str = "qualificar leads"
    business_name: str = ""
    business_domain: str = ""
    short_description: str = ""

    @classmethod
    def from_profile(cls, profile, tenant=None) -> "AssistantProfileContext":
        if profile is None:
            return cls()
        tenant_name = str(getattr(tenant, "name", "") or "").strip()
        business_name = str(getattr(profile, "business_name", "") or "").strip() or tenant_name
        return cls(
            name=str(getattr(profile, "name", "") or "Lívia").strip() or "Lívia",
            initial_message=str(
                getattr(profile, "initial_message", "")
                or "Olá! Sou a Lívia. Como posso te ajudar?"
            ).strip(),
            tone=str(getattr(profile, "tone", "") or cls.tone).strip(),
            primary_goal=str(getattr(profile, "primary_goal", "") or cls.primary_goal).strip(),
            business_name=business_name,
            business_domain=str(getattr(profile, "business_domain", "") or "").strip(),
            short_description=str(getattr(profile, "short_description", "") or "").strip(),
        )


class LiviaDecisionService:
    def __init__(
        self,
        lead_capture_service: LeadCaptureService | None = None,
        crm_dispatch_service: CRMDispatchService | None = None,
        handoff_service: HandoffService | None = None,
        ai_client: OpenAIChatClient | None = None,
    ):
        self.lead_capture_service = lead_capture_service or LeadCaptureService()
        self.crm_dispatch_service = crm_dispatch_service or CRMDispatchService()
        self.handoff_service = handoff_service or HandoffService()
        self.ai_client = ai_client or OpenAIChatClient()

    def generate_reply(
        self,
        history: Iterable[dict[str, str]],
        current_message: str,
        conversation=None,
        assistant_profile=None,
        knowledge_context: str | None = None,
    ) -> LiviaReply:
        tenant = getattr(conversation, "tenant", None)
        profile_context = AssistantProfileContext.from_profile(assistant_profile, tenant=tenant)
        discovery = analyze_message(current_message)
        classification = discovery.to_dict()
        intent = discovery.intent
        if knowledge_context is None:
            knowledge_context = build_knowledge_context(
                tenant,
                current_message,
                service_area=discovery.service_area,
                limit=2,
                conversation=conversation,
            ) if tenant is not None else ""
        else:
            knowledge_context = str(knowledge_context or "")
        has_commercial_interest = bool(discovery.has_commercial_interest)
        has_quote_request = bool(discovery.has_quote_request)
        has_support_request = bool(discovery.has_support_request)
        has_technical_question = bool(discovery.has_technical_question)
        if conversation is not None and should_lock_lead(conversation) and not can_start_new_cycle(conversation, current_message):
            decision = self._locked_lead_reply(intent if intent not in {"unknown", "greeting"} else "commercial_interest")
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        turn = classify_conversation_turn(
            current_message=current_message,
            history=history,
            conversation=conversation,
            discovery=discovery,
        )
        collection_gate = decide_collection(
            current_message=current_message,
            conversation=conversation,
            discovery=discovery,
        )
        if conversation is not None and collection_gate.should_collect and turn.kind == TurnKind.OTHER:
            return self._handle_qualification(
                intent=discovery.intent if discovery.intent not in {"unknown", "greeting"} else "commercial_interest",
                history=history,
                current_message=current_message,
                conversation=conversation,
                discovery=discovery,
                assistant_profile=assistant_profile,
                knowledge_context=knowledge_context,
                activate_collection=True,
            )
        if conversation is not None and turn.kind != TurnKind.OTHER:
            return self._handle_contextual_turn(
                turn=turn,
                intent=discovery.intent if discovery.intent not in {"unknown", "greeting", "contact_data"} else "commercial_interest",
                history=history,
                current_message=current_message,
                conversation=conversation,
                discovery=discovery,
                assistant_profile=assistant_profile,
                knowledge_context=knowledge_context,
            )

        if intent == "greeting":
            decision = LiviaReply(intent=intent, reply=profile_context.initial_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if intent == "technical_question":
            if self._should_answer_informatively_from_knowledge(discovery, knowledge_context):
                reply = build_contextual_reply(intent="technical_question")
                decision = LiviaReply(intent=intent, reply=self._with_knowledge(reply, knowledge_context))
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            if discovery.should_ask_discovery_question and discovery.suggested_next_question:
                decision = self._ask_discovery_question(
                    discovery, conversation, knowledge_context=knowledge_context,
                    assistant_profile=assistant_profile, tenant=tenant,
                )
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            reply = build_contextual_reply(intent="technical_question")
            decision = LiviaReply(intent=intent, reply=self._with_knowledge(reply, knowledge_context))
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if intent == "support_request":
            decision = LiviaReply(
                intent=intent,
                reply=self._with_knowledge(build_contextual_reply(intent="support_request"), knowledge_context),
            )
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if intent in {"quote_request", "commercial_interest"}:
            if conversation is not None and should_lock_lead(conversation) and not can_start_new_cycle(conversation, current_message):
                decision = self._locked_lead_reply(intent)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            collection = decide_collection(
                current_message=current_message,
                conversation=conversation,
                discovery=discovery,
            )
            if not collection.should_collect:
                if self._should_answer_informatively_from_knowledge(discovery, knowledge_context):
                    decision = self._handle_consultative_conversation(
                        intent=intent,
                        history=history,
                        current_message=current_message,
                        conversation=conversation,
                        discovery=discovery,
                        assistant_profile=assistant_profile,
                        knowledge_context=knowledge_context,
                    )
                    return decision
                if self._should_ask_profile_discovery(current_message, assistant_profile):
                    decision = self._ask_discovery_question(
                        discovery, conversation, knowledge_context=knowledge_context,
                        assistant_profile=assistant_profile, tenant=tenant, current_message=current_message,
                    )
                    decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                    return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
                if should_combine_kb_with_discovery(discovery, conversation, knowledge_context):
                    decision = self._ask_discovery_question(
                        discovery, conversation, knowledge_context=knowledge_context,
                        assistant_profile=assistant_profile, tenant=tenant, current_message=current_message,
                    )
                    decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                    return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
                if not discovery.should_collect_lead and discovery.should_ask_discovery_question and discovery.suggested_next_question:
                    # Prefer discovery when message is still vague and no profile-specific shape exists.
                    if len(str(current_message or "").split()) <= 4 and not discovery.should_answer_contextually:
                        decision = self._ask_discovery_question(
                            discovery, conversation, knowledge_context=knowledge_context,
                            assistant_profile=assistant_profile, tenant=tenant, current_message=current_message,
                        )
                        decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                        return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
                decision = self._handle_consultative_conversation(
                    intent=intent,
                    history=history,
                    current_message=current_message,
                    conversation=conversation,
                    discovery=discovery,
                    assistant_profile=assistant_profile,
                    knowledge_context=knowledge_context,
                )
                return decision
            if self._should_ask_profile_discovery(current_message, assistant_profile) and collection.trigger == CollectionTrigger.NONE:
                decision = self._ask_discovery_question(
                    discovery, conversation, knowledge_context=knowledge_context,
                    assistant_profile=assistant_profile, tenant=tenant, current_message=current_message,
                )
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            if should_combine_kb_with_discovery(discovery, conversation, knowledge_context) and not collection.should_collect:
                decision = self._ask_discovery_question(
                    discovery, conversation, knowledge_context=knowledge_context,
                    assistant_profile=assistant_profile, tenant=tenant, current_message=current_message,
                )
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            if self._should_answer_informatively_from_knowledge(discovery, knowledge_context) and not collection.should_collect:
                decision = LiviaReply(
                    intent=intent,
                    reply=self._with_knowledge(
                        build_conceptual_price_reply() if is_conceptual_price_question(current_message) else "",
                        knowledge_context,
                    ) or prefer_contextual_reply_over_fallback(
                        knowledge_context=knowledge_context or "",
                        current_message=current_message,
                        history=history,
                    ),
                )
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            return self._handle_qualification(
                intent=intent,
                history=history,
                current_message=current_message,
                conversation=conversation,
                discovery=discovery,
                assistant_profile=assistant_profile,
                knowledge_context=knowledge_context,
                activate_collection=True,
            )
        if intent == "contact_data":
            collection = decide_collection(
                current_message=current_message,
                conversation=conversation,
                discovery=discovery,
            )
            if collection.should_collect:
                return self._handle_qualification(
                    intent="contact_data",
                    history=history,
                    current_message=current_message,
                    conversation=conversation,
                    discovery=discovery,
                    assistant_profile=assistant_profile,
                    knowledge_context=knowledge_context,
                    activate_collection=True,
                )
            if has_basic_contact(current_message) and (has_quote_request or has_commercial_interest):
                # Contact alone during consultative mode keeps conversation going,
                # but if the visitor already provided contact + commercial context
                # after an active collection, qualification handles it above.
                decision = self._handle_consultative_conversation(
                    intent="commercial_interest",
                    history=history,
                    current_message=current_message,
                    conversation=conversation,
                    discovery=discovery,
                    assistant_profile=assistant_profile,
                    knowledge_context=knowledge_context,
                )
                return decision
            decision = LiviaReply(
                intent=intent,
                reply=build_contextual_reply(intent="contact_data"),
            )
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if has_basic_contact(current_message) and (has_commercial_interest or has_quote_request):
            if conversation is not None and should_lock_lead(conversation) and not can_start_new_cycle(conversation, current_message):
                decision = self._locked_lead_reply("contact_data")
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            collection = decide_collection(
                current_message=current_message,
                conversation=conversation,
                discovery=discovery,
            )
            if not collection.should_collect:
                return self._handle_consultative_conversation(
                    intent="commercial_interest",
                    history=history,
                    current_message=current_message,
                    conversation=conversation,
                    discovery=discovery,
                    assistant_profile=assistant_profile,
                    knowledge_context=knowledge_context,
                )
            return self._handle_qualification(
                intent="contact_data",
                history=history,
                current_message=current_message,
                conversation=conversation,
                discovery=discovery,
                assistant_profile=assistant_profile,
                knowledge_context=knowledge_context,
                activate_collection=True,
            )
        if has_support_request or has_technical_question:
            decision = LiviaReply(
                intent=intent,
                reply=self._with_knowledge(build_contextual_reply(intent=intent), knowledge_context),
            )
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if self._is_followup_from_history(history):
            need = ""
            if conversation is not None:
                try:
                    need = str(getattr(conversation.lead_draft, "need_summary", "") or "")
                except Exception:
                    need = ""
            reply = prefer_contextual_reply_over_fallback(
                knowledge_context=knowledge_context or "",
                need_summary=need,
                current_message=current_message,
                history=history,
            )
            decision = LiviaReply(
                intent="followup",
                reply=reply,
            )
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if discovery.should_ask_discovery_question and has_semantic_knowledge_block(knowledge_context) and not is_informational_knowledge_query(discovery):
            decision = self._ask_discovery_question(
                discovery,
                conversation,
                knowledge_context=knowledge_context,
                assistant_profile=assistant_profile,
                tenant=tenant,
            )
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        need = ""
        if conversation is not None:
            try:
                need = str(getattr(conversation.lead_draft, "need_summary", "") or "")
            except Exception:
                need = ""
        decision = LiviaReply(
            intent=intent,
            reply=prefer_contextual_reply_over_fallback(
                knowledge_context=knowledge_context or "",
                need_summary=need,
                current_message=current_message,
                history=history,
            ),
        )
        decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
        return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)


    def _handle_contextual_turn(
        self,
        *,
        turn,
        intent: str,
        history,
        current_message: str,
        conversation,
        discovery,
        assistant_profile,
        knowledge_context: str,
    ) -> LiviaReply:
        result = None
        if conversation is not None:
            result = self.lead_capture_service.capture_from_message(
                conversation=conversation,
                message=current_message,
                history=history,
            )
            if turn.kind == TurnKind.NAME_DEFERRED:
                mark_name_deferred(result.lead_draft)
        lead_draft = None if result is None else result.lead_draft
        if turn.kind == TurnKind.NAME_DEFERRED:
            reply = build_name_deferred_reply(lead_draft)
        elif turn.kind == TurnKind.DIRECT_QUESTION:
            reply = build_direct_question_reply(
                lead_draft,
                question_type=turn.question_type,
                current_message=current_message,
            )
        else:
            reply = build_enrichment_reply(
                lead_draft,
                snippet=turn.enrichment_snippet,
                current_message=current_message,
            )
        decision = LiviaReply(intent=intent, reply=self._with_knowledge(reply, knowledge_context))
        decision = self._finalize_handoff(decision, conversation, lead_draft, discovery, current_message)
        return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)

    def _finalize_ai_response(
        self,
        decision: LiviaReply,
        conversation,
        assistant_profile,
        discovery,
        current_message: str,
        history: Iterable[dict[str, str]],
        knowledge_context: str,
    ) -> LiviaReply:
        if not self._should_try_ai(assistant_profile):
            return decision
        tenant = getattr(conversation, "tenant", None)
        lead_state = str(getattr(conversation, "lead_state", "") or "")
        try:
            messages = build_livia_ai_prompt(
                tenant=tenant,
                assistant_profile=assistant_profile,
                message=current_message,
                conversation=conversation,
                discovery_result=discovery,
                lead_state=lead_state,
                knowledge_context=knowledge_context,
                deterministic_reply=decision.reply,
                history=list(history or []),
            )
            result = self.ai_client.create_chat_completion(messages=messages)
        except Exception as exc:  # pragma: no cover - defensive local guard
            logger.warning("livia_ai_finalize_failed error_type=%s", exc.__class__.__name__)
            return decision
        if result.success and result.text:
            return replace(decision, reply=result.text)
        return decision

    def _should_try_ai(self, assistant_profile) -> bool:
        return bool(getattr(settings, "LIVIA_AI_ENABLED", False)) and bool(getattr(assistant_profile, "use_ai", False))


    def _finalize_handoff(self, decision: LiviaReply, conversation, lead_draft, discovery, current_message: str) -> LiviaReply:
        result = self.handoff_service.create_or_update_handoff(
            conversation,
            lead_draft=lead_draft,
            discovery_result=discovery,
            message=current_message,
        )
        if result.handoff is None:
            return decision
        decision = replace(
            decision,
            handoff_request_id=result.handoff.pk,
            handoff_reason=result.handoff.reason,
        )
        if not result.created:
            return decision
        confirmation = self._handoff_confirmation(result.handoff)
        if confirmation.lower() in decision.reply.lower():
            return decision
        return replace(decision, reply=f"{confirmation}\n\n{decision.reply}")

    def _handoff_confirmation(self, handoff) -> str:
        has_contact = bool(handoff.visitor_phone or handoff.visitor_email)
        name = handoff.visitor_name or handoff.visitor_company
        if has_contact:
            if name:
                return f"Perfeito, {name}. Registrei o pedido de contato para a equipe retornar com o contexto da conversa."
            return "Perfeito. Registrei o pedido de contato para a equipe retornar com o contexto da conversa."
        return "Claro. Vou registrar seu pedido para atendimento humano. Para agilizar, me informe seu nome e um telefone ou e-mail de contato."

    def _locked_lead_reply(self, intent: str) -> LiviaReply:
        return LiviaReply(
            intent=intent,
            reply="Perfeito, já encaminhei seus dados para sequência do atendimento. Se for uma nova demanda, me diga que é um novo pedido.",
        )

    def _ask_discovery_question(self, discovery, conversation, knowledge_context: str = "", assistant_profile=None, tenant=None, current_message: str = "") -> LiviaReply:
        if conversation is not None:
            next_state = LeadState.COLLECT_NEED if discovery.intent in {"quote_request", "commercial_interest"} else LeadState.DISCOVERY
            set_state(conversation, next_state)
        profile_ctx = AssistantProfileContext.from_profile(assistant_profile, tenant=tenant or getattr(conversation, "tenant", None))
        if profile_ctx.business_domain:
            reply = resolve_discovery_question(
                discovery.service_area,
                business_domain=profile_ctx.business_domain,
                business_name=profile_ctx.business_name,
                short_description=profile_ctx.short_description,
                primary_goal=profile_ctx.primary_goal,
                current_message=current_message,
                knowledge_context=knowledge_context,
            )
        elif discovery.suggested_next_question:
            reply = discovery.suggested_next_question
        else:
            reply = resolve_discovery_question(
                discovery.service_area,
                business_domain=profile_ctx.business_domain,
                business_name=profile_ctx.business_name,
                short_description=profile_ctx.short_description,
                primary_goal=profile_ctx.primary_goal,
                current_message=current_message,
                knowledge_context=knowledge_context,
            )
        return LiviaReply(intent=discovery.intent, reply=self._with_knowledge(reply, knowledge_context))


    def _should_ask_profile_discovery(self, current_message: str, assistant_profile) -> bool:
        if assistant_profile is None:
            return False
        return should_ask_profile_discovery(
            current_message=current_message,
            business_domain=getattr(assistant_profile, "business_domain", ""),
            short_description=getattr(assistant_profile, "short_description", ""),
            primary_goal=getattr(assistant_profile, "primary_goal", ""),
        )

    def _with_knowledge(self, reply: str, knowledge_context: str) -> str:
        """
        Fundamenta a reply determinística com fatos curados do RAG.

        Com LIVIA_AI_ENABLED=False, sintetiza 1-2 frases naturais a partir dos
        chunks — sem dump de markdown, score, fonte ou metadados internos.
        """
        return synthesize_deterministic_reply(knowledge_context, base_reply=reply)

    def _synthesize_knowledge_reply(self, knowledge_context: str) -> str:
        """Compat: delega para a camada determinística compartilhada."""
        return synthesize_deterministic_reply(knowledge_context, base_reply="")

    def _knowledge_hints(self, knowledge_context: str) -> str:
        """Compat: extrai texto sintetizado já limpo."""
        return synthesize_deterministic_reply(knowledge_context, base_reply="")

    def _is_followup_from_history(self, history: Iterable[dict[str, str]]) -> bool:
        messages = list(history or [])
        if not messages:
            return False
        last_user = next((message for message in reversed(messages) if message.get("role") == "user"), None)
        if not last_user:
            return False
        return len(str(last_user.get("content") or "").strip()) <= 18

    def _handle_consultative_conversation(
        self,
        *,
        intent: str,
        history,
        current_message: str,
        conversation,
        discovery,
        assistant_profile,
        knowledge_context: str,
    ) -> LiviaReply:
        lead_draft = None
        if conversation is not None:
            result = self.lead_capture_service.capture_from_message(
                conversation=conversation,
                message=current_message,
                history=history,
            )
            lead_draft = result.lead_draft
            set_state(conversation, LeadState.DISCOVERY)
        if is_conceptual_price_question(current_message):
            reply = build_conceptual_price_reply(lead_draft)
        elif self._should_answer_informatively_from_knowledge(discovery, knowledge_context):
            # Com RAG, prioriza grounding + pergunta leve de contexto — sem
            # desviar para discovery genérico de "automatizar processo".
            reply = self._grounded_informational_followup(
                assistant_profile=assistant_profile,
                knowledge_context=knowledge_context,
                lead_draft=lead_draft,
                current_message=current_message,
            )
        else:
            reply = build_consultative_commercial_reply(
                lead_draft=lead_draft,
                current_message=current_message,
                history=history,
            )
        decision = LiviaReply(intent=intent, reply=self._with_knowledge(reply, knowledge_context))
        decision = self._finalize_handoff(decision, conversation, lead_draft, discovery, current_message)
        return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)

    def _grounded_informational_followup(
        self,
        *,
        assistant_profile,
        knowledge_context: str,
        lead_draft,
        current_message: str,
    ) -> str:
        """Follow-up curto após grounding RAG, sem hardcode de vertical alheia."""
        domain = normalize_text(
            " ".join(
                [
                    str(getattr(assistant_profile, "business_domain", "") or ""),
                    str(getattr(assistant_profile, "short_description", "") or ""),
                    str(getattr(assistant_profile, "business_name", "") or ""),
                    str(knowledge_context or "")[:400],
                ]
            )
        )
        stone = any(
            marker in domain
            for marker in (
                "marmor", "pedra", "granito", "marmore", "bancada", "cozinha",
                "banheiro", "escada", "gourmet", "pitondo", "quartzito",
            )
        )
        robotics = any(
            marker in domain
            for marker in (
                "robot", "xyron", "mitsubishi", "automacao", "automação", "clp", "ihm",
            )
        )
        if stone and not robotics:
            return (
                "Posso te orientar com base no material aprovado sobre pedras e projetos sob medida. "
                "Qual detalhe é mais importante agora: material, medidas ou acabamento?"
            )
        if robotics:
            return (
                "Trabalhamos com robótica de serviço e outras soluções documentadas. "
                "Se quiser, me conta o ambiente e o objetivo principal para eu afinar a orientação."
            )
        return build_consultative_commercial_reply(
            lead_draft=lead_draft,
            current_message=current_message,
            history=None,
        )

    def _handle_qualification(
        self,
        *,
        intent: str,
        history: Iterable[dict[str, str]],
        current_message: str,
        conversation,
        discovery=None,
        assistant_profile=None,
        knowledge_context: str = "",
        activate_collection: bool = False,
    ) -> LiviaReply:
        if conversation is None:
            decision = LiviaReply(intent=intent, reply=build_contextual_reply(intent=intent))
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if should_lock_lead(conversation) and not can_start_new_cycle(conversation, current_message):
            decision = LiviaReply(
                intent=intent,
                reply="Perfeito, já encaminhei seus dados para sequência do atendimento. Se for uma nova demanda, me diga que é um novo pedido.",
            )
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)

        if activate_collection:
            lead_seed = self.lead_capture_service.get_or_create_lead_draft(conversation)
            mark_collection_active(lead_seed)

        result = self.lead_capture_service.capture_from_message(
            conversation=conversation,
            message=current_message,
            history=history,
        )
        if activate_collection:
            mark_collection_active(result.lead_draft)
        reply = self.lead_capture_service.build_next_prompt(result.lead_draft, result.missing_fields, intent=intent, invalid_fields=result.invalid_fields)
        if result.is_qualified:
            reply = build_contextual_reply(intent=intent, missing_fields=[])
        decision = LiviaReply(intent=intent, reply=reply)
        decision = self._finalize_handoff(decision, conversation, result.lead_draft, discovery, current_message)
        return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)

    def _should_answer_informatively_from_knowledge(self, discovery, knowledge_context: str) -> bool:
        return has_semantic_knowledge_block(knowledge_context) and is_informational_knowledge_query(discovery)

    def _should_start_lead_from_contact(
        self,
        current_message: str,
        has_commercial_interest: bool,
        has_quote_request: bool,
    ) -> bool:
        if has_commercial_interest or has_quote_request:
            return True
        text = str(current_message or "").strip().lower()
        if not text or not has_basic_contact(current_message):
            return False
        return True
