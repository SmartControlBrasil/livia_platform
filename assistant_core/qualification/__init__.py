"""Regras de qualificação da Lívia."""

from .livia import (
    ContactSnapshot,
    extract_contact_snapshot,
    has_basic_contact,
    is_generic_value,
    is_valid_city,
    is_valid_company,
    is_valid_email,
    is_valid_name,
    is_valid_need_summary,
    is_valid_phone,
    looks_like_invalid_email,
    looks_like_invalid_phone,
    minimum_lead_data_met,
    normalize_text,
    strip_repetition_noise,
)

__all__ = [
    "ContactSnapshot",
    "extract_contact_snapshot",
    "has_basic_contact",
    "is_generic_value",
    "is_valid_city",
    "is_valid_company",
    "is_valid_email",
    "is_valid_name",
    "is_valid_need_summary",
    "is_valid_phone",
    "looks_like_invalid_email",
    "looks_like_invalid_phone",
    "minimum_lead_data_met",
    "normalize_text",
    "strip_repetition_noise",
]
