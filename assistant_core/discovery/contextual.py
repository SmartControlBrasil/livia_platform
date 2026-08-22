from __future__ import annotations

import re
import unicodedata

STOPWORDS = {
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos", "e", "em",
    "eu", "me", "meu", "minha", "o", "os", "para", "por", "que", "um", "uma", "uns", "umas",
    "voce", "voces", "preciso", "quero", "gostaria", "sobre", "orçamento", "orcamento",
}
ACTION_MARKERS = (
    "automatizar", "contratar", "criar", "desenvolver", "implantar", "comprar", "resolver",
    "fazer", "montar", "avaliar", "integrar", "configurar", "instalar", "consertar", "arrumar",
)
DETAIL_MARKERS = (
    "medida", "medidas", "foto", "fotos", "planta", "prazo", "origem", "destino", "volume",
    "quantidade", "modelo", "material", "metragem", "contato", "telefone", "email", "e-mail",
)


def resolve_discovery_question(
    service_area: str,
    *,
    business_domain: str = "",
    business_name: str = "",
    short_description: str = "",
    primary_goal: str = "",
    current_message: str = "",
    knowledge_context: str = "",
) -> str:
    _ = service_area, knowledge_context
    profile = BusinessProfileText(
        business_domain=business_domain,
        business_name=business_name,
        short_description=short_description,
        primary_goal=primary_goal,
    )
    extracted = extract_message_shape(current_message)

    if extracted.action and extracted.subject:
        pronoun = "nela" if extracted.article in {"a", "uma"} else "nele"
        article = extracted.article or "o"
        return f"Claro. Qual é {article} {extracted.subject} e o que você pretende {extracted.action} {pronoun}?"

    if extracted.subject and extracted.context:
        return f"Claro. Você já tem detalhes ou medidas aproximadas para {extracted.subject} {extracted.context}?"

    if extracted.subject:
        return f"Claro. Me conta um pouco mais sobre {extracted.subject}: qual é o objetivo e algum detalhe importante para avaliar?"

    if profile.business_domain:
        return f"Claro. Posso te ajudar com {profile.business_domain}. O que você precisa fazer ou resolver?"

    if profile.short_description:
        return "Claro. Posso te ajudar a avaliar melhor sua necessidade. O que você pretende fazer ou resolver?"

    if profile.business_name:
        return "Claro. Pode me contar um pouco mais sobre o que você precisa?"

    return "Claro. Pode me contar um pouco mais sobre o que você precisa fazer ou resolver?"


def should_ask_profile_discovery(*, current_message: str, business_domain: str = "", short_description: str = "", primary_goal: str = "") -> bool:
    profile_text = normalize_text(" ".join((business_domain, short_description)))
    if not profile_text.strip():
        return False
    normalized = normalize_text(current_message)
    if not normalized or _has_contact(normalized):
        return False
    if _looks_informational(normalized):
        return False
    shape = extract_message_shape(current_message)
    if shape.has_detail_marker:
        return False
    if shape.action and shape.subject:
        return True
    if shape.subject and shape.context:
        return True
    words = [word for word in normalized.split() if word not in STOPWORDS]
    return len(words) <= 5 and any(normalized.startswith(prefix) for prefix in ("preciso", "quero", "gostaria"))


def profile_relevance_terms(*, business_domain: str = "", short_description: str = "", primary_goal: str = "") -> set[str]:
    text = normalize_text(" ".join((business_domain, short_description, primary_goal)))
    return {word for word in re.findall(r"[a-z0-9-]{3,}", text) if word not in STOPWORDS}


class BusinessProfileText:
    def __init__(self, *, business_domain: str = "", business_name: str = "", short_description: str = "", primary_goal: str = ""):
        self.business_domain = str(business_domain or "").strip()
        self.business_name = str(business_name or "").strip()
        self.short_description = str(short_description or "").strip()
        self.primary_goal = str(primary_goal or "").strip()


class MessageShape:
    def __init__(self, *, action: str = "", subject: str = "", article: str = "", context: str = "", has_detail_marker: bool = False):
        self.action = action
        self.subject = subject
        self.article = article
        self.context = context
        self.has_detail_marker = has_detail_marker


def extract_message_shape(message: str) -> MessageShape:
    original = " ".join(str(message or "").strip().split())
    normalized = normalize_text(original)
    action = _extract_action(normalized)
    article, subject = _extract_subject(normalized)
    context = _extract_context(normalized)
    return MessageShape(
        action=action,
        subject=subject,
        article=article,
        context=context,
        has_detail_marker=any(marker in normalized for marker in DETAIL_MARKERS) or bool(re.search(r"\d", normalized)),
    )


def normalize_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", normalized)


def _extract_action(normalized: str) -> str:
    for action in ACTION_MARKERS:
        if re.search(rf"\b{re.escape(action)}\b", normalized):
            return action
    match = re.search(r"\b(?:preciso|quero|gostaria de|gostaria)\s+([a-z]{4,})\b", normalized)
    if match:
        candidate = match.group(1)
        if candidate not in STOPWORDS:
            return candidate
    return ""


def _extract_subject(normalized: str) -> tuple[str, str]:
    patterns = (
        r"\b(?:sobre|para|de)\s+(um|uma|o|a)\s+([a-z0-9-]{3,})",
        r"\b(?:um|uma|o|a)\s+([a-z0-9-]{3,})",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            if len(match.groups()) == 2:
                article, subject = match.group(1), match.group(2)
            else:
                article = ""
                subject = match.group(1)
            if subject not in STOPWORDS:
                return article, subject
    words = [word for word in re.findall(r"[a-z0-9-]{3,}", normalized) if word not in STOPWORDS and word not in ACTION_MARKERS]
    return "", words[-1] if words else ""


def _extract_context(normalized: str) -> str:
    match = re.search(r"\b(?:para|na|no|em)\s+(?:minha|minhas|meu|meus|uma|um|a|o)?\s*([a-z0-9-]{3,}(?:\s+[a-z0-9-]{3,})?)", normalized)
    if not match:
        return ""
    words = [word for word in match.group(1).split() if word not in STOPWORDS]
    if not words:
        return ""
    return "para " + " ".join(words[:2])


def _looks_informational(normalized: str) -> bool:
    prefixes = (
        "como ", "qual ", "quais ", "quando ", "onde ", "por que ", "porque ",
        "posso ", "tem ", "voces tem ", "voces trabalham", "trabalham com",
    )
    return normalized.endswith("?") or normalized.startswith(prefixes)


def _has_contact(normalized: str) -> bool:
    if "@" in normalized:
        return True
    digits = re.sub(r"\D", "", normalized)
    return len(digits) >= 10
