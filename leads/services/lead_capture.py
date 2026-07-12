from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from django.db import transaction

from assistant_core.qualification import (
    ContactSnapshot,
    extract_contact_snapshot,
    is_valid_city,
    is_valid_company,
    is_valid_email,
    is_valid_name,
    is_valid_need_summary,
    is_valid_phone,
    looks_like_invalid_email,
    looks_like_invalid_phone,
    minimum_lead_data_met,
)
from assistant_core.state import LeadState, next_state_after_message
from conversations.models import Conversation

from ..models import LeadDraft


@dataclass(frozen=True)
class LeadCaptureResult:
    lead_draft: LeadDraft
    missing_fields: list[str]
    is_qualified: bool
    extracted_snapshot: ContactSnapshot
    invalid_fields: list[str] = field(default_factory=list)
    state: str = LeadState.DISCOVERY


class LeadCaptureService:
    REQUIRED_FIELDS = ("name_or_company", "phone_or_email", "need_summary")
    GENERIC_NEED_PHRASES = (
        "quero orcamento",
        "quero orçamento",
        "preciso de orcamento",
        "preciso de orçamento",
        "quanto custa",
        "valor",
        "proposta",
        "orcamento",
        "orçamento",
    )
    NEED_CONTEXT_KEYWORDS = (
        "preciso",
        "quero",
        "orçamento",
        "orcamento",
        "problema",
        "erro",
        "falha",
        "automação",
        "automacao",
        "sistema",
        "plataforma",
        "suporte",
        "manutenção",
        "manutencao",
        "integração",
        "integracao",
        "projeto",
        "desenvolver",
    )

    def get_or_create_lead_draft(self, conversation: Conversation) -> LeadDraft:
        lead_draft, _ = LeadDraft.objects.get_or_create(
            conversation=conversation,
            defaults={"tenant": conversation.tenant},
        )
        return lead_draft

    @transaction.atomic
    def capture_from_message(
        self,
        conversation: Conversation,
        message: str,
        history: Iterable[dict[str, str]] | None = None,
    ) -> LeadCaptureResult:
        lead_draft = self.get_or_create_lead_draft(conversation)
        corpus = self._build_corpus(history=history, message=message)
        snapshot = extract_contact_snapshot(corpus)
        current_snapshot = extract_contact_snapshot(message)
        invalid_fields: list[str] = []

        self._merge_validated(lead_draft, "name", snapshot.name, is_valid_name, invalid_fields)
        self._merge_validated(lead_draft, "company", snapshot.company, is_valid_company, invalid_fields)
        self._merge_validated(lead_draft, "email", snapshot.email, is_valid_email, invalid_fields)
        self._merge_validated(lead_draft, "phone", snapshot.phone, is_valid_phone, invalid_fields)
        self._merge_validated(lead_draft, "city", snapshot.city, is_valid_city, invalid_fields)

        if current_snapshot.email and not is_valid_email(current_snapshot.email):
            self._append_invalid(invalid_fields, "email")
        elif looks_like_invalid_email(message):
            self._append_invalid(invalid_fields, "email")

        if current_snapshot.phone and not is_valid_phone(current_snapshot.phone):
            self._append_invalid(invalid_fields, "phone")
        elif looks_like_invalid_phone(message):
            self._append_invalid(invalid_fields, "phone")

        need_summary = self._extract_need_summary(history=history, message=message)
        if need_summary:
            lead_draft.need_summary = self._merge_text(lead_draft.need_summary, need_summary)
        elif self._message_is_vague_need(message) and not is_valid_need_summary(lead_draft.need_summary):
            self._append_invalid(invalid_fields, "need_summary")

        missing_fields = self.calculate_missing_fields(lead_draft)
        if lead_draft.status not in {LeadDraft.Status.SENT_TO_CRM, LeadDraft.Status.FAILED}:
            lead_draft.status = (
                LeadDraft.Status.QUALIFIED
                if minimum_lead_data_met(lead_draft)
                else LeadDraft.Status.DRAFT
            )
        lead_draft.save()

        state_snapshot = next_state_after_message(
            conversation,
            lead_draft,
            intent="",
            extracted_data=snapshot,
        )
        self._sync_conversation(conversation, lead_draft, state_snapshot.state)

        return LeadCaptureResult(
            lead_draft=lead_draft,
            missing_fields=missing_fields,
            is_qualified=lead_draft.status == LeadDraft.Status.QUALIFIED,
            extracted_snapshot=snapshot,
            invalid_fields=invalid_fields,
            state=state_snapshot.state,
        )

    def calculate_missing_fields(self, lead_draft: LeadDraft) -> list[str]:
        missing: list[str] = []
        if not self._has_need_summary(lead_draft):
            missing.append("need_summary")
        if not self._has_name_or_company(lead_draft):
            missing.append("name_or_company")
        if not self._has_phone_or_email(lead_draft):
            missing.append("phone_or_email")
        return missing

    def build_next_prompt(
        self,
        lead_draft: LeadDraft,
        missing_fields: list[str],
        intent: str = "",
        invalid_fields: list[str] | None = None,
    ) -> str:
        invalid_fields = invalid_fields or []
        if "name" in invalid_fields:
            return "Para eu registrar certinho, me passa seu nome real, por favor."
        if "company" in invalid_fields:
            return "Pode me passar o nome real da empresa? Assim eu deixo o atendimento bem encaminhado."
        if "phone" in invalid_fields:
            return "Esse telefone ficou incompleto. Me envia um WhatsApp com DDD, por favor."
        if "email" in invalid_fields:
            return "Esse e-mail não parece válido. Pode me enviar um e-mail completo?"
        if "need_summary" in invalid_fields:
            return "Claro. Me conta rapidinho do que você precisa e em qual contexto para eu direcionar melhor."

        if not missing_fields:
            return "Perfeito. Já tenho os dados essenciais para seguir com o atendimento."
        if intent == "budget" and "need_summary" in missing_fields:
            return "Perfeito. Antes do contato, me conta em uma frase qual é a sua necessidade principal."

        next_field = missing_fields[0]
        if next_field == "need_summary":
            return "Perfeito. Me conta em uma frase qual é a sua necessidade principal."
        if next_field == "name_or_company":
            return "Ótimo. Para eu dar sequência, qual é o seu nome ou o nome da empresa?"
        if next_field == "phone_or_email":
            return "Entendi. Me passa seu telefone/WhatsApp ou e-mail para eu continuar."
        return "Perfeito. Pode me passar mais um dado para eu continuar?"

    def _sync_conversation(self, conversation: Conversation, lead_draft: LeadDraft, state: str) -> None:
        changed_fields: list[str] = []
        field_values = {
            "visitor_name": lead_draft.name or lead_draft.company,
            "visitor_email": lead_draft.email,
            "visitor_phone": lead_draft.phone,
        }
        for field_name, value in field_values.items():
            if not hasattr(conversation, field_name):
                continue
            current_value = str(getattr(conversation, field_name, "") or "").strip()
            next_value = str(value or "").strip()
            if next_value and current_value != next_value:
                setattr(conversation, field_name, next_value)
                changed_fields.append(field_name)

        if hasattr(conversation, "is_qualified"):
            is_qualified = lead_draft.status in {LeadDraft.Status.QUALIFIED, LeadDraft.Status.SENT_TO_CRM}
            if conversation.is_qualified != is_qualified:
                conversation.is_qualified = is_qualified
                changed_fields.append("is_qualified")

        if hasattr(conversation, "lead_state") and conversation.lead_state != state:
            conversation.lead_state = state
            changed_fields.append("lead_state")

        if changed_fields:
            conversation.save(update_fields=changed_fields + ["updated_at"])

    def _has_name_or_company(self, lead_draft: LeadDraft) -> bool:
        return bool(
            (str(lead_draft.name or "").strip() and is_valid_name(lead_draft.name))
            or (str(lead_draft.company or "").strip() and is_valid_company(lead_draft.company))
        )

    def _has_phone_or_email(self, lead_draft: LeadDraft) -> bool:
        return bool(
            (str(lead_draft.phone or "").strip() and is_valid_phone(lead_draft.phone))
            or (str(lead_draft.email or "").strip() and is_valid_email(lead_draft.email))
        )

    def _has_need_summary(self, lead_draft: LeadDraft) -> bool:
        return is_valid_need_summary(lead_draft.need_summary)

    def _build_corpus(self, history: Iterable[dict[str, str]] | None, message: str) -> str:
        parts = [
            str(item.get("content") or "")
            for item in (history or [])
            if item.get("role") == "user"
        ]
        parts.append(str(message or ""))
        return " ".join(part for part in parts if part.strip())

    def _extract_need_summary(
        self,
        *,
        history: Iterable[dict[str, str]] | None,
        message: str,
    ) -> str:
        candidate = self._select_need_summary_candidate(history=history, message=message)
        text = self._strip_contact_noise(candidate)
        if not is_valid_need_summary(text):
            return ""
        return text[:500].strip()

    def _strip_contact_noise(self, text: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?:\+?\d[\d\s().-]{8,}\d)", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip(" ,.;:-")

    def _merge_text(self, current: str, new_value: str) -> str:
        current_value = str(current or "").strip()
        new_value = str(new_value or "").strip()
        if new_value:
            return new_value
        return current_value

    def _merge_validated(self, lead_draft: LeadDraft, field_name: str, new_value: str, validator, invalid_fields: list[str]) -> None:
        current_value = str(getattr(lead_draft, field_name, "") or "").strip()
        candidate = str(new_value or "").strip()
        if not candidate:
            return
        if not validator(candidate):
            self._append_invalid(invalid_fields, field_name)
            return
        if current_value and validator(current_value):
            return
        setattr(lead_draft, field_name, candidate)

    def _append_invalid(self, invalid_fields: list[str], field_name: str) -> None:
        if field_name not in invalid_fields:
            invalid_fields.append(field_name)

    def _is_generic_need_summary(self, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        return any(phrase == normalized for phrase in self.GENERIC_NEED_PHRASES)

    def _message_is_vague_need(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        return bool(cleaned and self._is_generic_need_summary(cleaned))

    def _select_need_summary_candidate(
        self,
        *,
        history: Iterable[dict[str, str]] | None,
        message: str,
    ) -> str:
        current_message = str(message or "").strip()
        if self._is_need_summary_candidate(current_message):
            return current_message

        for item in reversed(list(history or [])):
            if item.get("role") != "user":
                continue
            content = str(item.get("content") or "").strip()
            if self._is_need_summary_candidate(content):
                return content
        return ""

    def _is_need_summary_candidate(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        if self._is_generic_need_summary(cleaned):
            return False
        lowered = cleaned.lower()
        snapshot = extract_contact_snapshot(cleaned)
        if snapshot.has_any_contact() and not any(keyword in lowered for keyword in self.NEED_CONTEXT_KEYWORDS):
            return False
        return is_valid_need_summary(cleaned)
