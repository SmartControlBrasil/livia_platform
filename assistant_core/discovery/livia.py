from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DiscoveryResult:
    intent: str
    scenario: str = "unknown"
    service_area: str = "unknown"
    confidence: float = 0.5
    should_collect_lead: bool = False
    should_answer_contextually: bool = False
    should_ask_discovery_question: bool = False
    suggested_next_question: str = ""
    reason: str = ""
    normalized_text: str = ""
    has_contact_data: bool = False
    has_quote_request: bool = False
    has_commercial_interest: bool = False
    has_technical_question: bool = False
    has_support_request: bool = False
    has_greeting: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


GREETING_PATTERNS = (
    "oi",
    "ola",
    "bom dia",
    "boa tarde",
    "boa noite",
    "eai",
    "oii",
)

QUOTE_PATTERNS = (
    "orcamento",
    "preco",
    "valor",
    "quanto custa",
    "cotacao",
    "proposta",
)

COMMERCIAL_PATTERNS = (
    "contratar",
    "comprar",
    "servico",
    "solucao",
    "plataforma",
    "projeto",
    "desenvolver",
    "desenvolvimento",
    "implantar",
    "implantacao",
    "especialista",
    "visita",
    "visita tecnica",
    "atendimento comercial",
    "trabalham com",
    "voces trabalham",
    "voces tem",
    "quero um",
    "quero uma",
    "quero criar",
    "preciso contratar",
    "preciso de uma solucao",
    "quero uma solucao",
    "loja virtual",
    "e-commerce",
    "ecommerce",
)

NEED_ACTION_PATTERNS = (
    "preciso ",
    "quero ",
    "gostaria ",
    "tenho interesse",
    "estou procurando",
)

TECHNICAL_PATTERNS = (
    "erro",
    "falha",
    "problema",
    "nao funciona",
    "bug",
    "instalar",
    "configurar",
    "integrar",
    "tecnico",
    "parou",
    "dando erro",
    "nao liga",
    "sem comunicacao",
)

SUPPORT_PATTERNS = (
    "suporte",
    "ajuda",
    "assistencia",
    "meu sistema caiu",
    "nao consigo",
    "sem acesso",
    "fora do ar",
    "pos-venda",
    "como faco login",
    "como fazer login",
    "nao consigo login",
    "nao consigo acessar",
    "senha",
)

GENERIC_NEED_TEXTS = {
    "quero orcamento",
    "preciso de orcamento",
    "orcamento",
    "quanto custa",
    "valor",
    "preco",
    "quero uma proposta",
    "quero proposta",
}

GENERIC_DISCOVERY_QUESTION = (
    "Claro. Pode me contar um pouco mais sobre o que você precisa fazer ou resolver?"
)


def normalize_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", normalized)


def detect_intent(text: str) -> str:
    return analyze_message(text).intent


def classify_message(text: str) -> dict[str, object]:
    return analyze_message(text).to_dict()


def analyze_message(text: str) -> DiscoveryResult:
    normalized = normalize_text(text)
    if not normalized:
        return _result("unknown", normalized, reason="empty_message")

    has_greeting = _is_greeting(normalized)
    has_quote = _matches_any(normalized, QUOTE_PATTERNS)
    has_support = _matches_any(normalized, SUPPORT_PATTERNS)
    has_technical = _matches_any(normalized, TECHNICAL_PATTERNS)
    has_commercial = _matches_any(normalized, COMMERCIAL_PATTERNS) or _has_need_action(normalized)
    has_contact = _looks_like_contact(normalized)
    has_substantive_context = _has_substantive_context(normalized)
    generic_need = normalized in GENERIC_NEED_TEXTS

    if has_greeting:
        return _result(
            "greeting",
            normalized,
            scenario="greeting",
            confidence=0.95,
            has_contact_data=has_contact,
            has_quote_request=has_quote,
            has_commercial_interest=has_commercial,
            has_technical_question=has_technical,
            has_support_request=has_support,
            has_greeting=True,
            reason="short_greeting",
        )

    if has_support and not has_quote and not _has_visit_or_budget_marker(normalized):
        return _result(
            "support_request",
            normalized,
            scenario="support_request",
            confidence=0.9,
            should_answer_contextually=True,
            has_contact_data=has_contact,
            has_support_request=True,
            has_technical_question=has_technical,
            reason="support_without_commercial_intent",
        )

    if has_quote:
        from assistant_core.consultative_policy import detect_collection_trigger, is_conceptual_price_question
        from assistant_core.consultative_policy import CollectionTrigger

        conceptual_price = is_conceptual_price_question(text)
        informational = conceptual_price or _looks_informational(normalized) or any(
            marker in normalized for marker in ("prazo", "entrega", "tempo de", "quanto tempo", "demora", "como funciona")
        )
        explicit_collect = detect_collection_trigger(text) != CollectionTrigger.NONE
        should_collect = explicit_collect and not generic_need and not informational
        return _result(
            "quote_request",
            normalized,
            scenario="quote_request",
            confidence=0.9 if should_collect else 0.75,
            should_collect_lead=should_collect,
            should_ask_discovery_question=not should_collect and not informational,
            should_answer_contextually=informational,
            suggested_next_question=GENERIC_DISCOVERY_QUESTION,
            has_contact_data=has_contact,
            has_quote_request=True,
            has_commercial_interest=True,
            has_technical_question=has_technical,
            has_support_request=has_support,
            reason=(
                "quote_explicit_collection"
                if should_collect
                else ("quote_informational" if informational else "quote_needs_discovery")
            ),
        )

    if has_technical and not _has_visit_or_budget_marker(normalized):
        return _result(
            "technical_question",
            normalized,
            scenario="technical_question",
            confidence=0.82,
            should_answer_contextually=True,
            has_contact_data=has_contact,
            has_technical_question=True,
            has_support_request=has_support,
            reason="technical_without_forwarding",
        )

    if has_contact and has_commercial:
        return _result(
            "contact_data",
            normalized,
            scenario="contact_data",
            confidence=0.85,
            should_collect_lead=True,
            has_contact_data=True,
            has_commercial_interest=True,
            has_technical_question=has_technical,
            has_support_request=has_support,
            reason="contact_with_commercial_context",
        )

    if has_commercial:
        from assistant_core.consultative_policy import detect_collection_trigger, is_conceptual_price_question
        from assistant_core.consultative_policy import CollectionTrigger

        informational_question = _looks_informational(normalized) or is_conceptual_price_question(text)
        explicit_collect = detect_collection_trigger(text) != CollectionTrigger.NONE
        # commercial_interest alone must NOT start name/contact collection.
        should_collect = explicit_collect and not informational_question
        return _result(
            "commercial_interest",
            normalized,
            scenario="commercial_interest",
            confidence=0.8 if should_collect else 0.68,
            should_collect_lead=should_collect,
            should_ask_discovery_question=not should_collect and not informational_question,
            should_answer_contextually=informational_question or has_substantive_context,
            suggested_next_question=GENERIC_DISCOVERY_QUESTION,
            has_contact_data=has_contact,
            has_commercial_interest=True,
            has_technical_question=has_technical,
            has_support_request=has_support,
            reason=(
                "commercial_explicit_collection"
                if should_collect
                else ("commercial_consultative" if has_substantive_context else "commercial_needs_discovery")
            ),
        )

    if has_contact:
        return _result(
            "contact_data",
            normalized,
            scenario="contact_data",
            confidence=0.75,
            has_contact_data=True,
            reason="contact_marker",
        )

    return _result(
        "unknown",
        normalized,
        scenario="unknown",
        confidence=0.45,
        reason="no_clear_intent",
    )


def _result(
    intent: str,
    normalized_text: str,
    *,
    scenario: str = "unknown",
    service_area: str = "unknown",
    confidence: float = 0.5,
    should_collect_lead: bool = False,
    should_answer_contextually: bool = False,
    should_ask_discovery_question: bool = False,
    suggested_next_question: str = "",
    reason: str = "",
    has_contact_data: bool = False,
    has_quote_request: bool = False,
    has_commercial_interest: bool = False,
    has_technical_question: bool = False,
    has_support_request: bool = False,
    has_greeting: bool = False,
) -> DiscoveryResult:
    return DiscoveryResult(
        intent=intent,
        scenario=scenario,
        service_area=service_area,
        confidence=confidence,
        should_collect_lead=should_collect_lead,
        should_answer_contextually=should_answer_contextually,
        should_ask_discovery_question=should_ask_discovery_question,
        suggested_next_question=suggested_next_question,
        reason=reason,
        normalized_text=normalized_text,
        has_contact_data=has_contact_data,
        has_quote_request=has_quote_request,
        has_commercial_interest=has_commercial_interest,
        has_technical_question=has_technical_question,
        has_support_request=has_support_request,
        has_greeting=has_greeting,
    )


def _has_substantive_context(normalized_text: str) -> bool:
    words = [word for word in normalized_text.split() if len(word) > 2]
    if len(words) >= 3:
        return True
    return bool(re.search(r"\d", normalized_text))


def _has_need_action(normalized_text: str) -> bool:
    if normalized_text in GENERIC_NEED_TEXTS:
        return True
    if any(normalized_text.startswith(pattern) for pattern in NEED_ACTION_PATTERNS):
        return True
    return bool(re.search(r"\b(?:vendo|trabalho com|trabalhamos com|minha loja vende)\b", normalized_text))


def _has_visit_or_budget_marker(normalized_text: str) -> bool:
    return any(marker in normalized_text for marker in ("orcamento", "proposta", "visita", "diagnostico", "contratar", "comprar"))


def _looks_informational(normalized_text: str) -> bool:
    prefixes = ("como ", "qual ", "quais ", "quando ", "onde ", "posso ", "tem ", "voces tem ", "voces trabalham", "trabalham com", "quanto tempo", "da para", "da pra")
    return normalized_text.endswith("?") or normalized_text.startswith(prefixes)


def _is_greeting(normalized_text: str) -> bool:
    words = normalized_text.split()
    if len(words) > 5:
        return False
    # Word-boundary match avoids false positives like "escola" containing "ola".
    for pattern in GREETING_PATTERNS:
        if " " in pattern:
            if pattern in normalized_text:
                return True
        elif re.search(rf"(?<!\w){re.escape(pattern)}(?!\w)", normalized_text):
            return True
    return False


def _matches_any(normalized_text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in normalized_text for pattern in patterns)


def _looks_like_contact(normalized_text: str) -> bool:
    from assistant_core.conversation_turns import is_name_deferred

    if is_name_deferred(normalized_text):
        return False
    if "@" in normalized_text:
        return True
    digits = re.sub(r"\D", "", normalized_text)
    if len(digits) >= 10:
        return True
    return bool(re.search(r"\b(?:meu nome|sou |telefone|whatsapp|celular|email|e-mail|cidade)\b", normalized_text))
