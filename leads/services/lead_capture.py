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
        snapshot = extract_contact_snapshot(message)

        lead_draft.name = self._merge_text(lead_draft.name, snapshot.name)
        lead_draft.company = self._merge_text(lead_draft.company, snapshot.company)
        lead_draft.email = self._merge_text(lead_draft.email, snapshot.email)
        lead_draft.phone = self._merge_text(lead_draft.phone, snapshot.phone)
        lead_draft.city = self._merge_text(lead_draft.city, snapshot.city)
        lead_draft.need_summary = self._merge_text(lead_draft.need_summary, self._extract_need_summary(corpus, message))

        missing_fields = self.calculate_missing_fields(lead_draft)
        lead_draft.status = (
            LeadDraft.Status.QUALIFIED
            if self._has_minimum_data(lead_draft)
            else LeadDraft.Status.DRAFT
        )
        lead_draft.save()

        return LeadCaptureResult(
            lead_draft=lead_draft,
            missing_fields=missing_fields,
            is_qualified=lead_draft.status == LeadDraft.Status.QUALIFIED,
            extracted_snapshot=snapshot,
        )

    def calculate_missing_fields(self, lead_draft: LeadDraft) -> list[str]:
        missing: list[str] = []
        if not self._has_name_or_company(lead_draft):
            missing.append("nome")
        if not self._has_phone_or_email(lead_draft):
            missing.append("telefone ou e-mail")
        if not self._has_need_summary(lead_draft):
            missing.append("resumo da necessidade")
        return missing

    def build_next_prompt(self, lead_draft: LeadDraft, missing_fields: list[str]) -> str:
        if not missing_fields:
            return "Perfeito. Já tenho os dados mínimos para seguir com o atendimento."
        next_field = missing_fields[0]
        if next_field == "nome":
            return "Perfeito. Qual é o seu nome ou o nome da empresa?"
        if next_field == "telefone ou e-mail":
            return "Perfeito. Me passe seu telefone/WhatsApp ou e-mail para eu continuar."
        if next_field == "resumo da necessidade":
            return "Entendi. Me conte em uma frase qual é a sua necessidade principal."
        return "Perfeito. Pode me passar mais um dado para eu continuar?"

    def _has_minimum_data(self, lead_draft: LeadDraft) -> bool:
        return self._has_name_or_company(lead_draft) and self._has_phone_or_email(lead_draft) and self._has_need_summary(lead_draft)

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

    def _extract_need_summary(self, corpus: str, message: str) -> str:
        text = self._strip_contact_noise(corpus or message)
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
