from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from assistant_core.discovery import classify_message
from assistant_core.prompts import (
    BUDGET_REPLY,
    COMMERCIAL_REPLY,
    CONTACT_REPLY,
    DEFAULT_REPLY,
    GREETING_REPLY,
    TECHNICAL_REPLY,
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

        if intent == "greeting":
            return LiviaReply(intent=intent, reply=GREETING_REPLY)
        if intent == "budget":
            return self._handle_qualification(
                intent=intent,
                reply_prefix=BUDGET_REPLY,
                history=history,
                current_message=current_message,
                conversation=conversation,
            )
        if intent == "technical":
            return LiviaReply(intent=intent, reply=TECHNICAL_REPLY)
        if intent == "commercial":
            return self._handle_qualification(
                intent=intent,
                reply_prefix=COMMERCIAL_REPLY,
                history=history,
                current_message=current_message,
                conversation=conversation,
            )
        if intent == "contact" or has_basic_contact(current_message):
            return self._handle_qualification(
                intent="contact",
                reply_prefix=CONTACT_REPLY,
                history=history,
                current_message=current_message,
                conversation=conversation,
            )
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
        reply_prefix: str,
        history: Iterable[dict[str, str]],
        current_message: str,
        conversation,
    ) -> LiviaReply:
        if conversation is None:
            return LiviaReply(intent=intent, reply=reply_prefix)

        result = self.lead_capture_service.capture_from_message(
            conversation=conversation,
            message=current_message,
            history=history,
        )
        reply = self.lead_capture_service.build_next_prompt(result.lead_draft, result.missing_fields)
        if result.is_qualified:
            reply = (
                "Perfeito. Já tenho os dados mínimos para seguir com seu atendimento. "
                "Se quiser, posso continuar refinando a necessidade."
            )
        return LiviaReply(intent=intent, reply=reply)
