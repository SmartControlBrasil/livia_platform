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
    "tem robo",
    "quero um",
    "quero uma",
    "preciso de uma solucao",
    "quero uma solucao",
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

AUTOMATION_PATTERNS = (
    "clp",
    "ihm",
    "inversor",
    "servo",
    "scada",
    "supervisorio",
    "mitsubishi",
    "siemens",
    "weg",
    "rockwell",
    "allen bradley",
    "retrofit",
    "painel eletrico",
    "painel de automacao",
    "automacao industrial",
)

ROBOTICS_PATTERNS = (
    "robo",
    "robotica",
    "xyron",
    "liro",
    "hygibot",
    "hygi bot",
    "neobot",
    "hostbot",
    "robo de limpeza",
    "robo de atendimento",
    "robo de recepcao",
    "robo patrulha",
)

MAINTENANCE_PATTERNS = (
    "manutencao",
    "conserto",
    "consertar",
    "arrumar",
    "esteira",
    "bike",
    "bicicleta ergonometrica",
    "escada ergonometrica",
    "equipamento parado",
    "maquina parada",
    "visita tecnica",
    "academia",
)

SOFTWARE_WEB_PATTERNS = (
    "site",
    "sistema web",
    "sistema",
    "software",
    "dashboard",
    "crm",
    "agente de ia",
    "livia",
    "atlas",
    "automacao de atendimento",
    "portal",
    "app",
    "aplicativo",
    "ecommerce",
    "e-commerce",
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

AREA_QUESTIONS = {
    "automation": "Claro. Para eu te direcionar melhor: é automação com CLP/IHM, inversor, servo, SCADA, retrofit ou painel?",
    "robotics": "Perfeito. É para academia, indústria, hospital, condomínio ou outro ambiente?",
    "maintenance": "Entendi. É uma esteira residencial, profissional de academia ou equipamento industrial? E qual o problema principal?",
    "software_web": "Legal. Esse sistema ou site é para vendas, atendimento, operação interna, dashboard ou integração com IA?",
    "unknown": "Claro. Para eu te direcionar melhor: você precisa de automação industrial, robótica, manutenção técnica ou sistema web?",
}


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
    has_commercial = _matches_any(normalized, COMMERCIAL_PATTERNS)
    has_contact = _looks_like_contact(normalized)
    service_area = _detect_service_area(normalized)
    has_area_context = service_area != "unknown"
    has_substantive_context = _has_substantive_context(normalized, service_area)
    generic_need = normalized in GENERIC_NEED_TEXTS

    if has_greeting:
        return _result(
            "greeting",
            normalized,
            scenario="greeting",
            service_area=service_area,
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
            service_area=service_area,
            confidence=0.9,
            should_answer_contextually=True,
            has_contact_data=has_contact,
            has_support_request=True,
            has_technical_question=has_technical,
            reason="support_without_commercial_intent",
        )

    if has_quote:
        should_collect = has_substantive_context and not generic_need
        return _result(
            "quote_request",
            normalized,
            scenario="quote_request",
            service_area=service_area,
            confidence=0.9 if should_collect else 0.75,
            should_collect_lead=should_collect,
            should_ask_discovery_question=not should_collect,
            suggested_next_question=AREA_QUESTIONS.get(service_area, AREA_QUESTIONS["unknown"]),
            has_contact_data=has_contact,
            has_quote_request=True,
            has_commercial_interest=True,
            has_technical_question=has_technical,
            has_support_request=has_support,
            reason="quote_with_context" if should_collect else "quote_needs_discovery",
        )

    if has_technical and not _has_visit_or_budget_marker(normalized):
        return _result(
            "technical_question",
            normalized,
            scenario="technical_question" if service_area != "maintenance" else "maintenance",
            service_area=service_area,
            confidence=0.82,
            should_answer_contextually=True,
            should_ask_discovery_question=service_area == "maintenance",
            suggested_next_question=AREA_QUESTIONS.get(service_area, AREA_QUESTIONS["unknown"]),
            has_contact_data=has_contact,
            has_technical_question=True,
            has_support_request=has_support,
            reason="technical_without_forwarding",
        )

    has_area_interest = service_area in {"robotics", "maintenance"} and any(
        marker in normalized
        for marker in ("preciso", "quero", "voces", "trabalham", "tem ", "arrumar", "consertar", "manutencao")
    )
    if has_commercial or has_area_interest:
        should_collect = has_substantive_context and not _should_ask_area_discovery(normalized, service_area)
        return _result(
            "commercial_interest",
            normalized,
            scenario=service_area if service_area != "unknown" else "commercial_interest",
            service_area=service_area,
            confidence=0.8 if should_collect else 0.68,
            should_collect_lead=should_collect,
            should_ask_discovery_question=not should_collect,
            suggested_next_question=AREA_QUESTIONS.get(service_area, AREA_QUESTIONS["unknown"]),
            has_contact_data=has_contact,
            has_commercial_interest=True,
            has_technical_question=has_technical,
            has_support_request=has_support,
            reason="commercial_with_context" if should_collect else "commercial_needs_discovery",
        )

    if has_contact:
        return _result(
            "contact_data",
            normalized,
            scenario="contact_data",
            service_area=service_area,
            confidence=0.75,
            has_contact_data=True,
            reason="contact_marker",
        )

    return _result(
        "unknown",
        normalized,
        scenario="unknown",
        service_area=service_area,
        confidence=0.45,
        should_answer_contextually=has_area_context,
        should_ask_discovery_question=has_area_context,
        suggested_next_question=AREA_QUESTIONS.get(service_area, AREA_QUESTIONS["unknown"]) if has_area_context else "",
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


def _detect_service_area(normalized_text: str) -> str:
    if _matches_any(normalized_text, ROBOTICS_PATTERNS):
        return "robotics"
    if _matches_any(normalized_text, MAINTENANCE_PATTERNS):
        return "maintenance"
    if _matches_any(normalized_text, AUTOMATION_PATTERNS):
        return "automation"
    if _matches_any(normalized_text, SOFTWARE_WEB_PATTERNS) or re.search(r"\bia\b", normalized_text):
        return "software_web"
    return "unknown"


def _has_substantive_context(normalized_text: str, service_area: str) -> bool:
    if service_area != "unknown" and len(normalized_text.split()) >= 4:
        return True
    return False


def _should_ask_area_discovery(normalized_text: str, service_area: str) -> bool:
    if service_area == "robotics" and not any(marker in normalized_text for marker in ("academia", "industria", "hospital", "condominio", "recepcao", "atendimento", "patrulha")):
        return True
    if service_area == "maintenance" and not any(marker in normalized_text for marker in ("residencial", "academia", "industrial", "erro", "parou", "barulho", "nao liga")):
        return True
    if service_area == "software_web" and normalized_text in {"quero um site", "preciso de um site", "quero sistema", "preciso de sistema"}:
        return True
    return False


def _has_visit_or_budget_marker(normalized_text: str) -> bool:
    return any(marker in normalized_text for marker in ("orcamento", "proposta", "visita", "diagnostico", "contratar", "comprar"))


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
    return bool(re.search(r"(?:meu nome|sou|empresa|telefone|whatsapp|celular|email|e-mail|cidade)", normalized_text))
