from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from assistant_core.discovery import analyze_message
from assistant_core.prompts import (
    DEFAULT_REPLY,
    build_contextual_reply,
)
from assistant_core.qualification import has_basic_contact
from assistant_core.state import LeadState, can_start_new_cycle, set_state, should_lock_lead
from leads.services import CRMDispatchService, LeadCaptureService
from leads.services.handoff import HandoffService
from knowledge_base.rag.context_builder import build_knowledge_context


@dataclass(frozen=True)
class LiviaReply:
    intent: str
    reply: str


@dataclass(frozen=True)
class AssistantProfileContext:
    name: str = "Lívia"
    initial_message: str = "Olá! Sou a Lívia. Como posso te ajudar?"
    tone: str = "consultivo, claro e profissional"
    primary_goal: str = "qualificar leads"

    @classmethod
    def from_profile(cls, profile) -> "AssistantProfileContext":
        if profile is None:
            return cls()
        return cls(
            name=str(getattr(profile, "name", "") or "Lívia").strip() or "Lívia",
            initial_message=str(
                getattr(profile, "initial_message", "")
                or "Olá! Sou a Lívia. Como posso te ajudar?"
            ).strip(),
            tone=str(getattr(profile, "tone", "") or cls.tone).strip(),
            primary_goal=str(getattr(profile, "primary_goal", "") or cls.primary_goal).strip(),
        )


class LiviaDecisionService:
    def __init__(
        self,
        lead_capture_service: LeadCaptureService | None = None,
        crm_dispatch_service: CRMDispatchService | None = None,
        handoff_service: HandoffService | None = None,
    ):
        self.lead_capture_service = lead_capture_service or LeadCaptureService()
        self.crm_dispatch_service = crm_dispatch_service or CRMDispatchService()
        self.handoff_service = handoff_service or HandoffService()

    def generate_reply(
        self,
        history: Iterable[dict[str, str]],
        current_message: str,
        conversation=None,
        assistant_profile=None,
    ) -> LiviaReply:
        profile_context = AssistantProfileContext.from_profile(assistant_profile)
        discovery = analyze_message(current_message)
        classification = discovery.to_dict()
        intent = discovery.intent
        tenant = getattr(conversation, "tenant", None)
        knowledge_context = build_knowledge_context(
            tenant,
            current_message,
            service_area=discovery.service_area,
            limit=2,
        ) if tenant is not None else ""
        has_commercial_interest = bool(discovery.has_commercial_interest)
        has_quote_request = bool(discovery.has_quote_request)
        has_support_request = bool(discovery.has_support_request)
        has_technical_question = bool(discovery.has_technical_question)

        if intent == "greeting":
            return LiviaReply(intent=intent, reply=profile_context.initial_message)
        if intent == "technical_question":
            if discovery.should_ask_discovery_question and discovery.suggested_next_question:
                decision = self._ask_discovery_question(discovery, conversation, knowledge_context=knowledge_context)
                return self._finalize_handoff(decision, conversation, None, discovery, current_message)
            reply = build_contextual_reply(intent="technical_question")
            decision = LiviaReply(intent=intent, reply=self._with_knowledge(reply, knowledge_context))
            return self._finalize_handoff(decision, conversation, None, discovery, current_message)
        if intent == "support_request":
            decision = LiviaReply(intent=intent, reply=build_contextual_reply(intent="support_request"))
            return self._finalize_handoff(decision, conversation, None, discovery, current_message)
        if intent in {"quote_request", "commercial_interest"}:
            if conversation is not None and should_lock_lead(conversation) and not can_start_new_cycle(conversation, current_message):
                return self._locked_lead_reply(intent)
            if not discovery.should_collect_lead and discovery.should_ask_discovery_question:
                decision = self._ask_discovery_question(discovery, conversation, knowledge_context=knowledge_context)
                return self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._handle_qualification(
                intent=intent,
                history=history,
                current_message=current_message,
                conversation=conversation,
                discovery=discovery,
            )
        if intent == "contact_data":
            if self._should_start_lead_from_contact(current_message, has_commercial_interest, has_quote_request):
                return self._handle_qualification(
                    intent="contact_data",
                    history=history,
                    current_message=current_message,
                    conversation=conversation,
                    discovery=discovery,
                )
            decision = LiviaReply(
                intent=intent,
                reply=build_contextual_reply(intent="contact_data"),
            )
            return self._finalize_handoff(decision, conversation, None, discovery, current_message)
        if has_basic_contact(current_message) and (has_commercial_interest or has_quote_request):
            if conversation is not None and should_lock_lead(conversation) and not can_start_new_cycle(conversation, current_message):
                return self._locked_lead_reply("contact_data")
            if not discovery.should_collect_lead and discovery.should_ask_discovery_question:
                decision = self._ask_discovery_question(discovery, conversation, knowledge_context=knowledge_context)
                return self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._handle_qualification(
                intent="contact_data",
                history=history,
                current_message=current_message,
                conversation=conversation,
                discovery=discovery,
            )
        if has_support_request or has_technical_question:
            decision = LiviaReply(intent=intent, reply=build_contextual_reply(intent=intent))
            return self._finalize_handoff(decision, conversation, None, discovery, current_message)
        if self._is_followup_from_history(history):
            decision = LiviaReply(intent="followup", reply="Perfeito. Me conte um pouco mais sobre o contexto para eu te orientar.")
            return self._finalize_handoff(decision, conversation, None, discovery, current_message)
        decision = LiviaReply(intent=intent, reply=DEFAULT_REPLY)
        return self._finalize_handoff(decision, conversation, None, discovery, current_message)


    def _finalize_handoff(self, decision: LiviaReply, conversation, lead_draft, discovery, current_message: str) -> LiviaReply:
        result = self.handoff_service.create_or_update_handoff(
            conversation,
            lead_draft=lead_draft,
            discovery_result=discovery,
            message=current_message,
        )
        if result.handoff is None or not result.created:
            return decision
        confirmation = self._handoff_confirmation(result.handoff)
        if confirmation.lower() in decision.reply.lower():
            return decision
        return LiviaReply(intent=decision.intent, reply=f"{confirmation}\n\n{decision.reply}")

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

    def _ask_discovery_question(self, discovery, conversation, knowledge_context: str = "") -> LiviaReply:
        if conversation is not None:
            next_state = LeadState.COLLECT_NEED if discovery.intent in {"quote_request", "commercial_interest"} else LeadState.DISCOVERY
            set_state(conversation, next_state)
        reply = discovery.suggested_next_question or "Entendi. Me conta um pouco mais do contexto para eu te orientar melhor."
        return LiviaReply(intent=discovery.intent, reply=self._with_knowledge(reply, knowledge_context))

    def _with_knowledge(self, reply: str, knowledge_context: str) -> str:
        hints = self._knowledge_hints(knowledge_context)
        if not hints:
            return reply
        return f"{hints}\n\n{reply}"

    def _knowledge_hints(self, knowledge_context: str) -> str:
        lines = []
        for line in str(knowledge_context or "").splitlines():
            clean = line.strip()
            if not clean or clean.lower().startswith("base de conhecimento"):
                continue
            if ". " in clean and clean.split(". ", 1)[0].isdigit():
                clean = clean.split(". ", 1)[1]
            lines.append(clean)
            if len(lines) >= 2:
                break
        return " ".join(lines)

    def _is_followup_from_history(self, history: Iterable[dict[str, str]]) -> bool:
        messages = list(history or [])
        if not messages:
            return False
        last_user = next((message for message in reversed(messages) if message.get("role") == "user"), None)
        if not last_user:
            return False
        return len(str(last_user.get("content") or "").strip()) <= 18

    def _handle_qualification(
        self,
        *,
        intent: str,
        history: Iterable[dict[str, str]],
        current_message: str,
        conversation,
        discovery=None,
    ) -> LiviaReply:
        if conversation is None:
            return LiviaReply(intent=intent, reply=build_contextual_reply(intent=intent))
        if should_lock_lead(conversation) and not can_start_new_cycle(conversation, current_message):
            return LiviaReply(
                intent=intent,
                reply="Perfeito, já encaminhei seus dados para sequência do atendimento. Se for uma nova demanda, me diga que é um novo pedido.",
            )

        result = self.lead_capture_service.capture_from_message(
            conversation=conversation,
            message=current_message,
            history=history,
        )
        if result.is_qualified:
            self.crm_dispatch_service.dispatch_if_qualified(result.lead_draft)
        reply = self.lead_capture_service.build_next_prompt(result.lead_draft, result.missing_fields, intent=intent, invalid_fields=result.invalid_fields)
        if result.is_qualified:
            reply = build_contextual_reply(intent=intent, missing_fields=[])
        decision = LiviaReply(intent=intent, reply=reply)
        return self._finalize_handoff(decision, conversation, result.lead_draft, discovery, current_message)

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
