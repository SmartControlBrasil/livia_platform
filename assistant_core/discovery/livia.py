from __future__ import annotations

import re
import unicodedata

GREETING_PATTERNS = (
    "oi",
    "olá",
    "ola",
    "bom dia",
    "boa tarde",
    "boa noite",
    "eai",
    "oii",
)

BUDGET_PATTERNS = (
    "orcamento",
    "orçamento",
    "preco",
    "preço",
    "valor",
    "quanto custa",
    "cotacao",
    "cotação",
    "proposta",
)

TECHNICAL_PATTERNS = (
    "erro",
    "falha",
    "problema",
    "nao funciona",
    "não funciona",
    "bug",
    "instalar",
    "configurar",
    "integrar",
    "tecnico",
    "técnico",
    "suporte",
)

COMMERCIAL_PATTERNS = (
    "contratar",
    "comprar",
    "servico",
    "serviço",
    "solucao",
    "solução",
    "sistema",
    "plataforma",
    "projeto",
    "especialista",
    "visita",
    "atendimento",
    "interesse comercial",
)


def normalize_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", normalized)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def detect_intent(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return "default"
    if _matches_any(normalized, GREETING_PATTERNS) and len(normalized.split()) <= 5:
        return "greeting"
    if _matches_any(normalized, BUDGET_PATTERNS):
        return "budget"
    if _matches_any(normalized, TECHNICAL_PATTERNS):
        return "technical"
    if _matches_any(normalized, COMMERCIAL_PATTERNS):
        return "commercial"
    if _looks_like_contact(normalized):
        return "contact"
    return "default"


def _matches_any(normalized_text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in normalized_text for pattern in patterns)


def _looks_like_contact(normalized_text: str) -> bool:
    if "@" in normalized_text:
        return True
    digits = re.sub(r"\D", "", normalized_text)
    if len(digits) >= 10:
        return True
    return bool(re.search(r"\b(?:meu nome|sou|empresa|telefone|whatsapp|celular|email|e-mail)\b", normalized_text))


def classify_message(text: str) -> dict[str, str]:
    intent = detect_intent(text)
    return {
        "intent": intent,
        "normalized_text": normalize_text(text),
    }
