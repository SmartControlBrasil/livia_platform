from __future__ import annotations

import re
from dataclasses import dataclass

from assistant_core.discovery import analyze_message
from conversations.models import Message


@dataclass(frozen=True)
class ConversationSummary:
    title: str
    need_summary: str
    service_area: str
    intent: str
    urgency: str
    products_or_services: tuple[str, ...]
    visitor_context: str
    collected_fields: tuple[str, ...]
    conversation_notes: tuple[str, ...]
    recommended_next_step: str
    source_page: str
    tenant_slug: str


AREA_LABELS = {
    "automation": "automação",
    "robotics": "robótica",
    "maintenance": "manutenção",
    "software_web": "sistema web",
    "support": "suporte",
    "unknown": "indefinido",
}

PRODUCT_RULES = (
    ("CLP", ("clp",)),
    ("IHM", ("ihm",)),
    ("inversor", ("inversor",)),
    ("servo", ("servo",)),
    ("SCADA", ("scada",)),
    ("Mitsubishi", ("mitsubishi",)),
    ("retrofit", ("retrofit",)),
    ("painel", ("painel",)),
    ("robô de limpeza", ("robo de limpeza", "robô de limpeza", "hygibot", "liro")),
    ("Xyron", ("xyron",)),
    ("esteira", ("esteira",)),
    ("bike", ("bike",)),
    ("escada ergométrica", ("escada ergonometrica", "escada ergométrica")),
    ("site", ("site",)),
    ("dashboard", ("dashboard",)),
    ("CRM", ("crm",)),
    ("agente de IA", ("agente de ia", " ia ")),
    ("Lívia", ("livia", "lívia")),
    ("Atlas", ("atlas",)),
)


def build_conversation_summary(conversation, lead_draft=None) -> ConversationSummary:
    messages = _conversation_messages(conversation)
    user_messages = _substantive_user_messages(messages)
    corpus = _build_corpus(user_messages, lead_draft)
    discovery = analyze_message(corpus)
    service_area = _resolve_service_area(discovery.service_area, corpus)
    need_summary = _resolve_need_summary(lead_draft, user_messages)
    products = _extract_products_or_services(corpus)
    collected_fields = _collected_fields(lead_draft)
    urgency = _detect_urgency(corpus, service_area)
    notes = _conversation_notes(user_messages)
    return ConversationSummary(
        title=_build_title(service_area, discovery.intent),
        need_summary=need_summary,
        service_area=service_area,
        intent=discovery.intent,
        urgency=urgency,
        products_or_services=products,
        visitor_context=_build_visitor_context(lead_draft),
        collected_fields=collected_fields,
        conversation_notes=notes,
        recommended_next_step=_recommended_next_step(discovery.intent, service_area, urgency, corpus),
        source_page=str(getattr(conversation, "source_page", "") or "").strip(),
        tenant_slug=str(getattr(getattr(conversation, "tenant", None), "slug", "") or "").strip(),
    )


def format_conversation_summary_notes(summary: ConversationSummary) -> str:
    collected = ", ".join(summary.collected_fields) if summary.collected_fields else "nenhum dado validado ainda"
    products = ", ".join(summary.products_or_services) if summary.products_or_services else "não identificado"
    origin_parts = [summary.tenant_slug or "tenant indefinido"]
    if summary.source_page:
        origin_parts.append(summary.source_page)

    lines = [
        "Resumo da Lívia:",
        f"- Interesse: {AREA_LABELS.get(summary.service_area, summary.service_area or 'indefinido')}",
        f"- Necessidade: {summary.need_summary}",
        f"- Produtos/serviços citados: {products}",
        f"- Urgência: {summary.urgency}",
        f"- Dados coletados: {collected}",
        f"- Origem: {' | '.join(origin_parts)}",
        "- Conversa:",
    ]
    if summary.conversation_notes:
        lines.extend(f"  - {note}" for note in summary.conversation_notes)
    else:
        lines.append("  - Sem histórico detalhado registrado.")
    lines.append(f"- Próximo passo sugerido: {summary.recommended_next_step}")
    return "\n".join(lines)


def _conversation_messages(conversation):
    if conversation is None:
        return []
    return list(conversation.messages.exclude(role=Message.Role.SYSTEM).order_by("created_at", "id"))


def _substantive_user_messages(messages) -> list[str]:
    values: list[str] = []
    for message in messages:
        if message.role != Message.Role.USER:
            continue
        value = _sanitize(message.content)
        if not value or _is_contact_only(value):
            continue
        values.append(value)
    return values


def _build_corpus(user_messages: list[str], lead_draft) -> str:
    parts = []
    if lead_draft is not None:
        parts.extend(
            str(value or "").strip()
            for value in (lead_draft.need_summary, lead_draft.city)
            if str(value or "").strip()
        )
    parts.extend(user_messages)
    return " ".join(parts)


def _resolve_need_summary(lead_draft, user_messages: list[str]) -> str:
    if lead_draft is not None and str(lead_draft.need_summary or "").strip():
        return str(lead_draft.need_summary).strip()[:500]
    if user_messages:
        return user_messages[0][:500]
    return "Necessidade ainda em detalhamento com a Lívia."


def _resolve_service_area(service_area: str, corpus: str) -> str:
    if service_area and service_area != "unknown":
        return service_area
    normalized = _normalize(corpus)
    if "suporte" in normalized or "login" in normalized:
        return "support"
    return "unknown"


def _extract_products_or_services(corpus: str) -> tuple[str, ...]:
    normalized = _normalize(f" {corpus} ")
    found: list[str] = []
    for label, markers in PRODUCT_RULES:
        if any(marker in normalized for marker in markers) and label not in found:
            found.append(label)
    return tuple(found[:8])


def _collected_fields(lead_draft) -> tuple[str, ...]:
    if lead_draft is None:
        return tuple()
    labels = (
        ("name", "nome"),
        ("company", "empresa"),
        ("phone", "telefone"),
        ("email", "e-mail"),
        ("city", "cidade"),
    )
    return tuple(label for field_name, label in labels if str(getattr(lead_draft, field_name, "") or "").strip())


def _build_visitor_context(lead_draft) -> str:
    if lead_draft is None:
        return "Sem LeadDraft associado."
    parts = []
    if lead_draft.name:
        parts.append(f"nome {lead_draft.name}")
    if lead_draft.company:
        parts.append(f"empresa {lead_draft.company}")
    if lead_draft.city:
        parts.append(f"cidade {lead_draft.city}")
    return "; ".join(parts) if parts else "Dados do visitante ainda incompletos."


def _conversation_notes(user_messages: list[str]) -> tuple[str, ...]:
    notes = []
    for message in user_messages[-6:]:
        if message not in notes:
            notes.append(message[:220])
    return tuple(notes)


def _detect_urgency(corpus: str, service_area: str) -> str:
    normalized = _normalize(corpus)
    if any(marker in normalized for marker in ("parou", "parada", "urgente", "sem funcionar", "fora do ar")):
        return "alta"
    if service_area == "maintenance" and any(marker in normalized for marker in ("erro", "falha", "problema")):
        return "média"
    return "normal"


def _recommended_next_step(intent: str, service_area: str, urgency: str, corpus: str) -> str:
    normalized = _normalize(corpus)
    if urgency == "alta":
        return "Retornar por telefone/WhatsApp para triagem de urgência e avaliar visita técnica."
    if service_area == "maintenance":
        return "Confirmar equipamento, sintoma, marca/modelo e avaliar visita técnica ou diagnóstico remoto."
    if service_area == "automation":
        return "Alinhar escopo técnico de automação, equipamento envolvido e objetivo do orçamento."
    if service_area == "robotics":
        return "Confirmar ambiente de uso, aplicação esperada e próximos passos comerciais."
    if service_area == "software_web":
        return "Agendar conversa para detalhar escopo, integrações, prazo e expectativa de investimento."
    if "orcamento" in normalized or "orçamento" in normalized or intent == "quote_request":
        return "Retornar contato para entender escopo e preparar proposta comercial."
    return "Retornar contato para continuar a qualificação com base no histórico."


def _build_title(service_area: str, intent: str) -> str:
    area = AREA_LABELS.get(service_area, service_area or "indefinido")
    if intent == "quote_request":
        return f"Pedido de orçamento - {area}"
    return f"Lead Lívia - {area}"


def _is_contact_only(text: str) -> bool:
    value = str(text or "").strip()
    normalized = _normalize(value)
    if normalized in {"ok", "sim", "nao", "não", "obrigado", "obrigada"}:
        return True
    if re.fullmatch(r"[+()\d .-]{8,20}", value):
        return True
    if re.fullmatch(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", value, flags=re.IGNORECASE):
        return True
    return False


def _sanitize(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if any(marker in cleaned.lower() for marker in ("api_key", "bearer ", "token:", "traceback")):
        return ""
    return cleaned[:1200]


def _normalize(text: str) -> str:
    return str(text or "").strip().lower()
