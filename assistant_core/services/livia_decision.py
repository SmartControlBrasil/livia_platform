from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from assistant_core.discovery import classify_message
from assistant_core.prompts import (
    DEFAULT_REPLY,
    build_contextual_reply,
)
from assistant_core.qualification import has_basic_contact
from assistant_core.state import can_start_new_cycle, should_lock_lead
from leads.services import CRMDispatchService, LeadCaptureService


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
    ):
        self.lead_capture_service = lead_capture_service or LeadCaptureService()
        self.crm_dispatch_service = crm_dispatch_service or CRMDispatchService()

    def generate_reply(
        self,
        history: Iterable[dict[str, str]],
        current_message: str,
        conversation=None,
        assistant_profile=None,
    ) -> LiviaReply:
        profile_context = AssistantProfileContext.from_profile(assistant_profile)
        classification = classify_message(current_message)
        intent = classification["intent"]
        has_commercial_interest = bool(classification.get("has_commercial_interest"))
        has_quote_request = bool(classification.get("has_quote_request"))
        has_support_request = bool(classification.get("has_support_request"))
        has_technical_question = bool(classification.get("has_technical_question"))

        if intent == "greeting":
            return LiviaReply(intent=intent, reply=profile_context.initial_message)
        if intent == "technical_question":
            return LiviaReply(intent=intent, reply=build_contextual_reply(intent="technical_question"))
        if intent == "support_request":
            return LiviaReply(intent=intent, reply=build_contextual_reply(intent="support_request"))
        if intent == "quote_request":
            return self._handle_qualification(
                intent=intent,
                history=history,
                current_message=current_message,
                conversation=conversation,
            )
        if intent == "commercial_interest":
            return self._handle_qualification(
                intent=intent,
                history=history,
                current_message=current_message,
                conversation=conversation,
            )
        if intent == "contact_data":
            if self._should_start_lead_from_contact(current_message, has_commercial_interest, has_quote_request):
                return self._handle_qualification(
                    intent="contact_data",
                    history=history,
                    current_message=current_message,
                    conversation=conversation,
                )
            return LiviaReply(
                intent=intent,
                reply=build_contextual_reply(intent="contact_data"),
            )
        if has_basic_contact(current_message) and (has_commercial_interest or has_quote_request):
            return self._handle_qualification(
                intent="contact_data",
                history=history,
                current_message=current_message,
                conversation=conversation,
            )
        if has_support_request or has_technical_question:
            return LiviaReply(intent=intent, reply=build_contextual_reply(intent=intent))
        if self._is_followup_from_history(history):
            return LiviaReply(intent="followup", reply="Perfeito. Me conte um pouco mais sobre o contexto para eu te orientar.")
        return LiviaReply(intent=intent, reply=DEFAULT_REPLY)

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
        return LiviaReply(intent=intent, reply=reply)

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
