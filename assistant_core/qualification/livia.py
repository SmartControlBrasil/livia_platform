from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ContactSnapshot:
    name: str = ""
    company: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""

    def has_any_contact(self) -> bool:
        return any([self.name, self.company, self.email, self.phone, self.city])


def extract_contact_snapshot(text: str) -> ContactSnapshot:
    normalized = str(text or "").strip()
    email = _extract_email(normalized)
    phone = _extract_phone(normalized)
    name = _extract_name(normalized)
    company = _extract_company(normalized)
    city = _extract_city(normalized)
    return ContactSnapshot(
        name=name,
        company=company,
        email=email,
        phone=phone,
        city=city,
    )


def has_basic_contact(text: str) -> bool:
    snapshot = extract_contact_snapshot(text)
    return snapshot.has_any_contact()


def _extract_email(text: str) -> str:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.IGNORECASE)
    return match.group(0) if match else ""


def _extract_phone(text: str) -> str:
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 10:
        return digits
    return ""


def _extract_name(text: str) -> str:
    lowered = text.lower()
    if lowered.startswith("meu nome e "):
        return text[11:].strip()
    if lowered.startswith("meu nome é "):
        return text[11:].strip()
    if lowered.startswith("sou "):
        return text[4:].strip()
    return ""


def _extract_company(text: str) -> str:
    match = re.search(r"(?:empresa|companhia|companhia é|empresa é)\s*[:\-]?\s*(.+)$", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _extract_city(text: str) -> str:
    match = re.search(r"(?:cidade|sou de)\s*[:\-]?\s*(.+)$", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""
