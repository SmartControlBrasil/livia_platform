from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from assistant_core.discovery import classify_message
from assistant_core.prompts import (
    DEFAULT_REPLY,
    build_contextual_reply,
)
from assistant_core.qualification import has_basic_contact
from leads.services import LeadCaptureService


@dataclass(frozen=True)
class LiviaReply:
    intent: str
    reply: str


class LiviaDecisionService:
    def __init__(self, lead_capture_service: LeadCaptureService | None = None):
        self.lead_capture_service = lead_capture_service or LeadCaptureService()

    def generate_reply(
        self,
        history: Iterable[dict[str, str]],
        current_message: str,
        conversation=None,
    ) -> LiviaReply:
        classification = classify_message(current_message)
        intent = classification["intent"]
        has_commercial_interest = bool(classification.get("has_commercial_interest"))
        has_quote_request = bool(classification.get("has_quote_request"))
        has_support_request = bool(classification.get("has_support_request"))
        has_technical_question = bool(classification.get("has_technical_question"))

        if intent == "greeting":
            return LiviaReply(intent=intent, reply=build_contextual_reply(intent="greeting"))
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

        result = self.lead_capture_service.capture_from_message(
            conversation=conversation,
            message=current_message,
            history=history,
        )
        reply = self.lead_capture_service.build_next_prompt(result.lead_draft, result.missing_fields, intent=intent)
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
        return bool(text and has_basic_contact(current_message) and any(marker in text for marker in ("preciso", "quero", "orçamento", "orcamento", "proposta", "sistema", "solução", "solucao")))
