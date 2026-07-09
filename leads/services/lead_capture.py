from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from django.db import transaction

from assistant_core.qualification import ContactSnapshot, extract_contact_snapshot
from conversations.models import Conversation

from ..models import LeadDraft


@dataclass(frozen=True)
class LeadCaptureResult:
    lead_draft: LeadDraft
    missing_fields: list[str]
    is_qualified: bool
    extracted_snapshot: ContactSnapshot


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

        lead_draft.name = self._merge_text(lead_draft.name, snapshot.name)
        lead_draft.company = self._merge_text(lead_draft.company, snapshot.company)
        lead_draft.email = self._merge_text(lead_draft.email, snapshot.email)
        lead_draft.phone = self._merge_text(lead_draft.phone, snapshot.phone)
        lead_draft.city = self._merge_text(lead_draft.city, snapshot.city)
        lead_draft.need_summary = self._merge_text(
            lead_draft.need_summary,
            self._extract_need_summary(history=history, message=message),
        )

        missing_fields = self.calculate_missing_fields(lead_draft)
        if lead_draft.status not in {LeadDraft.Status.SENT_TO_CRM, LeadDraft.Status.FAILED}:
            lead_draft.status = (
                LeadDraft.Status.QUALIFIED
                if self._has_minimum_data(lead_draft)
                else LeadDraft.Status.DRAFT
            )
        lead_draft.save()
        self._sync_conversation(conversation, lead_draft)

        return LeadCaptureResult(
            lead_draft=lead_draft,
            missing_fields=missing_fields,
            is_qualified=lead_draft.status == LeadDraft.Status.QUALIFIED,
            extracted_snapshot=snapshot,
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

    def build_next_prompt(self, lead_draft: LeadDraft, missing_fields: list[str], intent: str = "") -> str:
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

    def _has_minimum_data(self, lead_draft: LeadDraft) -> bool:
        return self._has_name_or_company(lead_draft) and self._has_phone_or_email(lead_draft) and self._has_need_summary(lead_draft)

    def _sync_conversation(self, conversation: Conversation, lead_draft: LeadDraft) -> None:
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
            is_qualified = lead_draft.status == LeadDraft.Status.QUALIFIED
            if conversation.is_qualified != is_qualified:
                conversation.is_qualified = is_qualified
                changed_fields.append("is_qualified")

        if changed_fields:
            conversation.save(update_fields=changed_fields + ["updated_at"])

    def _has_name_or_company(self, lead_draft: LeadDraft) -> bool:
        return bool(str(lead_draft.name or "").strip() or str(lead_draft.company or "").strip())

    def _has_phone_or_email(self, lead_draft: LeadDraft) -> bool:
        return bool(str(lead_draft.phone or "").strip() or str(lead_draft.email or "").strip())

    def _has_need_summary(self, lead_draft: LeadDraft) -> bool:
        return bool(str(lead_draft.need_summary or "").strip())

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
        if self._is_generic_need_summary(text):
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

    def _is_generic_need_summary(self, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        return any(phrase == normalized for phrase in self.GENERIC_NEED_PHRASES)

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
        if len(cleaned) < 25:
            return False
        lowered = cleaned.lower()
        snapshot = extract_contact_snapshot(cleaned)
        if snapshot.has_any_contact() and not any(keyword in lowered for keyword in self.NEED_CONTEXT_KEYWORDS):
            return False
        return True
