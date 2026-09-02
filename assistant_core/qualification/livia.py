from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


INVALID_GENERIC_VALUES = {
    "",
    "nao informado",
    "não informado",
    "nao informada",
    "não informada",
    "sim",
    "ok",
    "quero",
    "quero sim",
    "gostaria",
    "pode ser",
    "orcamento",
    "orçamento",
    "quero orcamento",
    "quero orçamento",
    "preciso de orcamento",
    "preciso de orçamento",
    "atendimento",
    "quero atendimento",
    "teste",
    "test",
    "fulano",
    "ciclano",
    "beltrano",
    "cliente",
    "usuario",
    "usuário",
    "empresa",
    "minha empresa",
    "sem empresa",
    "nao tenho",
    "não tenho",
}

INVALID_NAME_SNIPPETS = (
    "empresa",
    "orcamento",
    "orçamento",
    "proposta",
    "sistema",
    "plataforma",
    "suporte",
    "atendimento",
    "erro",
    "problema",
    "automacao",
    "automação",
    "manutencao",
    "manutenção",
)

INVALID_COMPANY_OR_CITY_SNIPPETS = (
    "quero contato",
    "quero atendimento",
    "quero orçamento",
    "quero orcamento",
    "preciso de suporte",
    "preciso de atendimento",
    "falar com especialista",
    "pode agendar",
    "sistema",
    "plataforma",
    "telefone",
    "whatsapp",
    "email",
    "e-mail",
    "todo o brasil",
    "brasil todo",
    "nacional",
    "orcamento",
    "orçamento",
    "proposta",
    "cotacao",
    "cotação",
    "loja virtual",
    "preciso",
    "gostaria",
    "vendo",
    "trabalho",
    "quanto tempo",
    "quanto custa",
)

INVALID_CITY_VALUES = {
    "brasil",
    "nacional",
    "todo brasil",
    "todo o brasil",
    "sistema",
    "app",
    "aplicativo",
    "empresa",
    "cliente",
    "clientes",
    "atendimento",
}

REPETITION_NOISE_PATTERNS = (
    r"\bja falei\b",
    r"\bjá falei\b",
    r"\bcomo falei\b",
    r"\beu ja falei\b",
    r"\beu já falei\b",
    r"\bconforme falei\b",
)

GENERIC_NEED_PHRASES = {
    "quero orcamento",
    "quero orçamento",
    "preciso de orcamento",
    "preciso de orçamento",
    "quanto custa",
    "valor",
    "proposta",
    "orcamento",
    "orçamento",
    "quero",
    "sim",
    "ok",
}

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
    "clp",
    "ihm",
    "inversor",
    "servo",
    "scada",
    "mitsubishi",
    "retrofit",
    "painel",
    "robo",
    "robô",
    "robotica",
    "robótica",
    "xyron",
    "liro",
    "hygibot",
    "esteira",
    "bike",
    "academia",
    "site",
    "loja",
    "virtual",
    "ecommerce",
    "e-commerce",
    "catalogo",
    "catálogo",
    "produtos",
    "portal",
    "aplicativo",
    "dashboard",
    "crm",
    "agente",
    # marmoraria / pedras naturais (Pitondo e verticais similares)
    "cozinha",
    "bancada",
    "pia",
    "cooktop",
    "ilha",
    "frontao",
    "frontão",
    "banheiro",
    "lavabo",
    "cuba",
    "nicho",
    "escada",
    "gourmet",
    "churrasqueira",
    "granito",
    "marmore",
    "mármore",
    "quartzito",
    "pedra",
    "marmoraria",
    "revestimento",
    "acabamento",
    "medidas",
    "planta",
)


@dataclass(frozen=True)
class ContactSnapshot:
    name: str = ""
    company: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""

    def has_any_contact(self) -> bool:
        return any([self.name, self.company, self.email, self.phone, self.city])


def normalize_text(value) -> str:
    normalized = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", normalized)


def strip_repetition_noise(value) -> str:
    cleaned = str(value or "").strip()
    for pattern in REPETITION_NOISE_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip(" ,.-")


def is_generic_value(value) -> bool:
    return normalize_text(value) in {normalize_text(item) for item in INVALID_GENERIC_VALUES}


def is_valid_email(value) -> bool:
    if is_generic_value(value):
        return False
    return bool(re.fullmatch(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", str(value or "").strip(), re.IGNORECASE))


def is_valid_phone(value) -> bool:
    if is_generic_value(value):
        return False
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) in {12, 13} and digits.startswith("55"):
        digits = digits[2:]
    if len(digits) not in {10, 11}:
        return False
    if len(set(digits)) <= 2 and digits[-4:] in {"0000", "1111", "9999"}:
        return digits in {"11999999999"}
    return True


def is_valid_name(value) -> bool:
    cleaned = strip_repetition_noise(value)
    normalized = normalize_text(cleaned)
    if is_generic_value(cleaned):
        return False
    if any(snippet in normalized for snippet in INVALID_NAME_SNIPPETS):
        return False
    if re.search(r"\d", cleaned):
        return False
    return bool(re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{1,119}", cleaned))


def is_valid_company(value) -> bool:
    return _is_valid_company_or_city(value, allow_numbers=True, invalid_values={"empresa", "minha empresa", "teste"})


def is_valid_city(value) -> bool:
    if normalize_text(value) in {normalize_text(item) for item in INVALID_CITY_VALUES}:
        return False
    return _is_valid_company_or_city(value, allow_numbers=False, invalid_values=INVALID_CITY_VALUES)


def is_valid_need_summary(value) -> bool:
    cleaned = strip_repetition_noise(value)
    normalized = normalize_text(cleaned)
    if not cleaned or normalized in {normalize_text(item) for item in GENERIC_NEED_PHRASES}:
        return False
    context_keywords = tuple(
        normalize_text(item)
        for item in NEED_CONTEXT_KEYWORDS
        if normalize_text(item) not in {"quero", "preciso", "orcamento", "orçamento"}
    )
    has_context = any(keyword in normalized for keyword in context_keywords) or bool(re.search(r"\bia\b", normalized))
    if not has_context:
        return False
    if len(cleaned) < 18 and not re.search(
        r"\b(?:site|loja|clp|ihm|robo|robô|ia|bancada|cozinha|banheiro|escada|granito|marmore|mármore|nicho|gourmet)\b",
        normalized,
    ):
        return False
    return True


def minimum_lead_data_met(lead_draft) -> bool:
    has_name_or_company = bool(
        (str(getattr(lead_draft, "name", "") or "").strip() and is_valid_name(getattr(lead_draft, "name", "")))
        or (str(getattr(lead_draft, "company", "") or "").strip() and is_valid_company(getattr(lead_draft, "company", "")))
    )
    has_contact = bool(
        (str(getattr(lead_draft, "phone", "") or "").strip() and is_valid_phone(getattr(lead_draft, "phone", "")))
        or (str(getattr(lead_draft, "email", "") or "").strip() and is_valid_email(getattr(lead_draft, "email", "")))
    )
    return has_name_or_company and has_contact and is_valid_need_summary(getattr(lead_draft, "need_summary", ""))


def extract_contact_snapshot(text: str) -> ContactSnapshot:
    normalized = str(text or "").strip()
    return ContactSnapshot(
        name=_extract_name(normalized),
        company=_extract_company(normalized),
        email=_extract_email(normalized),
        phone=_extract_phone(normalized),
        city=_extract_city(normalized),
    )


def infer_pending_field_values(message: str, pending_field: str) -> dict[str, str]:
    """Interpreta resposta curta ao próximo campo pedido (ex.: 'Maria Silva' após pedir nome)."""
    text = strip_repetition_noise(str(message or "").strip(" .,-"))
    pending = str(pending_field or "").strip()
    if not text or not pending or "?" in text or len(text) > 80:
        return {}

    normalized = normalize_text(text)
    reject_markers = (
        "preciso",
        "quero",
        "gostaria",
        "orcamento",
        "orçamento",
        "proposta",
        "cotacao",
        "cotação",
        "quanto",
        "vendo",
        "trabalho",
        "site",
        "loja",
        "automat",
        "integr",
        "prazo",
        "tempo",
        "custa",
        "valor",
    )
    if pending == "name_or_company":
        if _extract_email(text) or _extract_phone(text):
            return {}
        company_markers = ("empresa", "sou da", "trabalho na", "da empresa")
        has_company_marker = any(marker in normalized for marker in company_markers)
        if not has_company_marker and any(marker in normalized for marker in reject_markers):
            return {}
        name_candidate = re.sub(
            r"^(?:meu nome é|meu nome e|me chamo|sou o|sou a|sou|nome)\s+",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip(" .,-")
        company_candidate = re.sub(
            r"^(?:a empresa é|a empresa e|empresa|sou da|trabalho na|da)\s+",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip(" .,-")
        if has_company_marker:
            if is_valid_company(company_candidate) and len(company_candidate.split()) <= 6:
                return {"company": company_candidate}
            return {}
        if is_valid_name(name_candidate) and 1 <= len(name_candidate.split()) <= 4:
            return {"name": name_candidate}
        # Resposta nua curta sem marcadores comerciais: pode ser empresa (ex.: "Ferragens Silva").
        if (
            is_valid_company(company_candidate)
            and 1 <= len(company_candidate.split()) <= 4
            and not any(marker in normalize_text(company_candidate) for marker in reject_markers)
        ):
            return {"company": company_candidate}
        return {}

    if pending == "phone_or_email":
        email = _extract_email(text)
        if email and is_valid_email(email):
            return {"email": email}
        phone = _extract_phone(text)
        if phone and is_valid_phone(phone):
            return {"phone": phone}
        digits = re.sub(r"\D", "", text)
        if digits and is_valid_phone(digits):
            return {"phone": digits}
        return {}

    if pending in {"name", "company", "email", "phone", "city"}:
        mapped = {
            "name": ("name", _extract_name(text) or text),
            "company": ("company", _extract_company(text) or text),
            "email": ("email", _extract_email(text)),
            "phone": ("phone", _extract_phone(text) or re.sub(r"\D", "", text)),
            "city": ("city", _extract_city(text) or text),
        }
        field_name, value = mapped[pending]
        validators = {
            "name": is_valid_name,
            "company": is_valid_company,
            "email": is_valid_email,
            "phone": is_valid_phone,
            "city": is_valid_city,
        }
        candidate = str(value or "").strip()
        if field_name in {"name", "company"} and any(marker in normalize_text(candidate) for marker in reject_markers):
            return {}
        if candidate and validators[field_name](candidate):
            return {field_name: candidate}
    return {}


def has_basic_contact(text: str) -> bool:
    return extract_contact_snapshot(text).has_any_contact()


def looks_like_invalid_email(text: str) -> bool:
    raw = str(text or "")
    if is_valid_email(_extract_email(raw)):
        return False
    return bool(re.search(r"\b(?:email|e-mail|mail)\b", raw, re.IGNORECASE) or re.search(r"\S+@\S*", raw))


def looks_like_invalid_phone(text: str) -> bool:
    raw = str(text or "")
    digits = re.sub(r"\D", "", raw)
    if is_valid_phone(digits):
        return False
    if re.fullmatch(r"\D*\d{3,9}\D*", raw.strip()):
        return True
    return bool(digits and len(digits) < 10 and re.search(r"\b(?:telefone|whatsapp|celular|fone)\b", raw, re.IGNORECASE))


def _extract_email(text: str) -> str:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.IGNORECASE)
    return match.group(0) if match else ""


def _extract_phone(text: str) -> str:
    match = re.search(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?\d{4,5}[\s.-]?\d{4}", text)
    if not match:
        return ""
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) in {12, 13} and digits.startswith("55"):
        digits = digits[2:]
    return digits


def _extract_name(text: str) -> str:
    match = re.search(r"(?:meu nome e|meu nome é|sou)\s*[:\-]?\s*([^,;]+)", text, re.IGNORECASE)
    if not match:
        return ""
    candidate = match.group(1).strip()
    candidate = re.split(r"\s+[eE]\s+(?:meu|minha|meus|minhas|preciso|quero)\b", candidate, maxsplit=1)[0].strip()
    candidate = re.split(r"[.!?]\s*(?:preciso|quero|tenho|busco)\b", candidate, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return candidate


def _extract_company(text: str) -> str:
    match = re.search(r"(?:empresa|companhia)\s*(?:é|e|:)?\s+([^,;]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"\b(?:da|de|do)\s+([A-ZÀ-Ý0-9][A-Za-zÀ-ÿ0-9 .&'-]{1,80})", text)
    return match.group(1).strip() if match else ""


def _extract_city(text: str) -> str:
    match = re.search(r"(?:cidade|sou de|estou em|fica em)\s*[:\-]?\s*(.+)$", text, re.IGNORECASE)
    if not match:
        return ""
    return re.split(r"[,;]", match.group(1).strip(), maxsplit=1)[0].strip()


def _is_valid_company_or_city(value, *, allow_numbers: bool, invalid_values) -> bool:
    cleaned = strip_repetition_noise(value)
    normalized = normalize_text(cleaned)
    invalid_normalized = {normalize_text(item) for item in invalid_values}
    if is_generic_value(cleaned) or normalized in invalid_normalized:
        return False
    if any(snippet in normalized for snippet in tuple(normalize_text(item) for item in INVALID_COMPANY_OR_CITY_SNIPPETS)):
        return False
    if normalized.startswith("de ") or normalized.startswith("da ") or normalized.startswith("do "):
        return False
    digits = re.sub(r"\D", "", cleaned)
    if not allow_numbers and digits:
        return False
    if len(digits) >= 8:
        return False
    pattern = r"[A-Za-z0-9À-ÿ][A-Za-z0-9À-ÿ .&/'-]{1,159}" if allow_numbers else r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{1,119}"
    return bool(re.fullmatch(pattern, cleaned))
