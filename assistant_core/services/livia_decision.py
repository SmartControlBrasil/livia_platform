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
from assistant_core.qualification import extract_contact_snapshot, has_basic_contact


@dataclass(frozen=True)
class LiviaReply:
    intent: str
    reply: str


class LiviaDecisionService:
    def generate_reply(self, history: Iterable[dict[str, str]], current_message: str) -> LiviaReply:
        classification = classify_message(current_message)
        intent = classification["intent"]

        if intent == "greeting":
            return LiviaReply(intent=intent, reply=GREETING_REPLY)
        if intent == "budget":
            return LiviaReply(intent=intent, reply=BUDGET_REPLY)
        if intent == "technical":
            return LiviaReply(intent=intent, reply=TECHNICAL_REPLY)
        if intent == "commercial":
            return LiviaReply(intent=intent, reply=COMMERCIAL_REPLY)
        if intent == "contact" or has_basic_contact(current_message):
            snapshot = extract_contact_snapshot(current_message)
            reply = CONTACT_REPLY
            if snapshot.email or snapshot.phone:
                reply = "Obrigado! Já registrei seu contato. Se quiser, me diga sua empresa e cidade para eu continuar."
            return LiviaReply(intent="contact", reply=reply)
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
