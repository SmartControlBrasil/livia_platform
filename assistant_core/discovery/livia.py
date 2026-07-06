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

QUOTE_PATTERNS = (
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

COMMERCIAL_PATTERNS = (
    "contratar",
    "comprar",
    "servico",
    "serviço",
    "solucao",
    "solução",
    "plataforma",
    "projeto",
    "desenvolver",
    "desenvolvimento",
    "implantar",
    "implantacao",
    "implantação",
    "especialista",
    "visita",
    "atendimento",
    "preciso de uma solucao",
    "preciso de uma solução",
    "quero uma solucao",
    "quero uma solução",
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
)

SUPPORT_PATTERNS = (
    "suporte",
    "ajuda",
    "atendimento",
    "assistencia",
    "assistência",
    "meu sistema caiu",
    "nao consigo",
    "não consigo",
    "sem acesso",
    "parou",
    "fora do ar",
    "pós-venda",
    "pos-venda",
)

CONTACT_PATTERNS = (
    "meu nome",
    "sou ",
    "empresa",
    "telefone",
    "whatsapp",
    "celular",
    "email",
    "e-mail",
    "mail",
    "cidade",
    "sou de",
)


def normalize_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", normalized)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def detect_intent(text: str) -> str:
    classification = classify_message(text)
    return classification["intent"]


def classify_message(text: str) -> dict[str, object]:
    normalized = normalize_text(text)
    if not normalized:
        return _result("unknown", normalized)

    has_greeting = _is_greeting(normalized)
    has_quote = _matches_any(normalized, QUOTE_PATTERNS)
    has_support = _matches_any(normalized, SUPPORT_PATTERNS)
    has_technical = _matches_any(normalized, TECHNICAL_PATTERNS)
    has_commercial = _matches_any(normalized, COMMERCIAL_PATTERNS)
    has_contact = _looks_like_contact(normalized)

    if has_quote:
        return _result("quote_request", normalized, has_contact, has_quote, has_commercial, has_technical, has_support, has_greeting)
    if has_support:
        return _result("support_request", normalized, has_contact, has_quote, has_commercial, has_technical, has_support, has_greeting)
    if has_technical:
        return _result("technical_question", normalized, has_contact, has_quote, has_commercial, has_technical, has_support, has_greeting)
    if has_commercial:
        return _result("commercial_interest", normalized, has_contact, has_quote, has_commercial, has_technical, has_support, has_greeting)
    if has_contact:
        return _result("contact_data", normalized, has_contact, has_quote, has_commercial, has_technical, has_support, has_greeting)
    if has_greeting:
        return _result("greeting", normalized, has_contact, has_quote, has_commercial, has_technical, has_support, has_greeting)
    return _result("unknown", normalized, has_contact, has_quote, has_commercial, has_technical, has_support, has_greeting)


def _result(
    intent: str,
    normalized_text: str,
    has_contact_data: bool = False,
    has_quote_request: bool = False,
    has_commercial_interest: bool = False,
    has_technical_question: bool = False,
    has_support_request: bool = False,
    has_greeting: bool = False,
) -> dict[str, object]:
    return {
        "intent": intent,
        "normalized_text": normalized_text,
        "has_contact_data": has_contact_data,
        "has_quote_request": has_quote_request,
        "has_commercial_interest": has_commercial_interest,
        "has_technical_question": has_technical_question,
        "has_support_request": has_support_request,
        "has_greeting": has_greeting,
    }


def _is_greeting(normalized_text: str) -> bool:
    return _matches_any(normalized_text, GREETING_PATTERNS) and len(normalized_text.split()) <= 5


def _matches_any(normalized_text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in normalized_text for pattern in patterns)


def _looks_like_contact(normalized_text: str) -> bool:
    if "@" in normalized_text:
        return True
    digits = re.sub(r"\D", "", normalized_text)
    if len(digits) >= 10:
        return True
    return bool(re.search(r"\b(?:meu nome|sou|empresa|telefone|whatsapp|celular|email|e-mail|cidade)\b", normalized_text))
