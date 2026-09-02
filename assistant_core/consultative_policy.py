from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from assistant_core.conversation_turns import (
    build_enrichment_reply,
    is_direct_question,
    is_name_deferred,
    is_need_enrichment,
    merge_need_summaries,
    normalize_text,
)
from assistant_core.state import LeadState, get_current_state

COLLECTION_ACTIVE_KEY = "collection_active"

BUDGET_TRIGGERS = (
    "quero um orcamento",
    "quero uma cotacao",
    "quero uma proposta",
    "gostaria de um orcamento",
    "gostaria de uma cotacao",
    "gostaria de uma proposta",
    "preciso de um orcamento",
    "preciso de uma cotacao",
    "preciso de uma proposta",
    "pode preparar uma proposta",
    "pode mandar um orcamento",
    "pode enviar um orcamento",
    "quero saber quanto ficaria",
    "quero orcamento",
    "quero cotacao",
    "quero proposta",
    "orcamento para",
    "cotacao para",
    "proposta para",
)

HIRE_TRIGGERS = (
    "quero contratar",
    "quero fechar",
    "quero comprar",
    "quero seguir com o projeto",
    "quero seguir com",
    "vamos fechar",
    "quero fechar o projeto",
)

HUMAN_TRIGGERS = (
    "quero falar com alguem",
    "quero falar com um especialista",
    "quero falar com especialista",
    "quero falar com vendedor",
    "quero falar com um vendedor",
    "quero atendimento",
    "quero um atendimento",
    "quero atendimento humano",
    "quero contato comercial",
    "falar com especialista",
    "falar com atendente",
    "falar com vendedor",
    "atendimento humano",
    "pode pedir para alguem me ligar",
    "pode me ligar",
    "chama no whatsapp",
    "chama no zap",
    "me passa para um especialista",
)

PRICE_QUESTION_MARKERS = (
    "quanto custa",
    "qual o preco",
    "qual o preço",
    "qual valor",
    "quanto fica",
    "quanto sairia",
    "qual o valor",
)


class CollectionTrigger(str, Enum):
    NONE = "none"
    BUDGET = "budget"
    HIRE = "hire"
    HUMAN = "human"


@dataclass(frozen=True)
class CollectionDecision:
    should_collect: bool
    trigger: CollectionTrigger = CollectionTrigger.NONE
    reason: str = ""


def is_conceptual_price_question(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if _has_explicit_actionable_budget(normalized):
        return False
    looks_like_q = "?" in str(text or "") or normalized.startswith(("quanto ", "qual ", "quais "))
    if not looks_like_q:
        return False
    return any(marker in normalized for marker in PRICE_QUESTION_MARKERS)


def _has_explicit_actionable_budget(normalized: str) -> bool:
    if any(term in normalized for term in HUMAN_TRIGGERS):
        return True
    if any(term in normalized for term in HIRE_TRIGGERS):
        return True
    if any(term in normalized for term in BUDGET_TRIGGERS):
        return True
    return bool(re.search(r"\b(quero|preciso|gostaria|pode)\b.{0,40}\b(orcamento|cotacao|proposta)\b", normalized))


def detect_collection_trigger(text: str) -> CollectionTrigger:
    normalized = normalize_text(text)
    if not normalized:
        return CollectionTrigger.NONE
    looks_like_price_q = (
        ("?" in str(text or "") or normalized.startswith(("quanto ", "qual ", "quais ")))
        and any(marker in normalized for marker in PRICE_QUESTION_MARKERS)
        and not _has_explicit_actionable_budget(normalized)
    )
    if looks_like_price_q:
        return CollectionTrigger.NONE
    if any(term in normalized for term in HUMAN_TRIGGERS):
        return CollectionTrigger.HUMAN
    if any(term in normalized for term in HIRE_TRIGGERS):
        return CollectionTrigger.HIRE
    if any(term in normalized for term in BUDGET_TRIGGERS):
        return CollectionTrigger.BUDGET
    if re.search(r"\b(quero|preciso|gostaria|pode)\b.{0,40}\b(orcamento|cotacao|proposta)\b", normalized):
        return CollectionTrigger.BUDGET
    return CollectionTrigger.NONE


def is_explicit_collection_trigger(text: str) -> bool:
    return detect_collection_trigger(text) != CollectionTrigger.NONE


def is_human_handoff_request(text: str) -> bool:
    return detect_collection_trigger(text) == CollectionTrigger.HUMAN


def collection_already_active(conversation, lead_draft=None) -> bool:
    lead = lead_draft
    if lead is None and conversation is not None:
        try:
            lead = conversation.lead_draft
        except Exception:
            lead = None
    if lead is not None:
        data = getattr(lead, "qualification_data", None) or {}
        if isinstance(data, dict) and data.get(COLLECTION_ACTIVE_KEY):
            return True
    # Soft capture during consultative mode may advance lead_state to
    # COLLECT_NAME_COMPANY without opening explicit name/contact collection.
    # Only treat later commercial states (or the explicit flag above) as active.
    state = get_current_state(conversation) if conversation is not None else LeadState.DISCOVERY
    return state in {LeadState.COLLECT_CONTACT, LeadState.OFFER_HANDOFF}


def mark_collection_active(lead_draft) -> None:
    if lead_draft is None or not hasattr(lead_draft, "qualification_data"):
        return
    data = dict(getattr(lead_draft, "qualification_data", None) or {})
    if data.get(COLLECTION_ACTIVE_KEY) is True:
        return
    data[COLLECTION_ACTIVE_KEY] = True
    lead_draft.qualification_data = data
    update_fields = ["qualification_data"]
    if hasattr(lead_draft, "updated_at"):
        update_fields.append("updated_at")
    lead_draft.save(update_fields=update_fields)


def decide_collection(*, current_message: str, conversation=None, lead_draft=None, discovery=None) -> CollectionDecision:
    trigger = detect_collection_trigger(current_message)
    if collection_already_active(conversation, lead_draft=lead_draft):
        return CollectionDecision(True, trigger=trigger if trigger != CollectionTrigger.NONE else CollectionTrigger.BUDGET, reason="collection_already_active")
    if trigger != CollectionTrigger.NONE:
        return CollectionDecision(True, trigger=trigger, reason=f"explicit_{trigger.value}")
    # Visitor volunteered contact data together with commercial/need context.
    if discovery is not None and getattr(discovery, "has_contact_data", False):
        if getattr(discovery, "has_commercial_interest", False) or getattr(discovery, "has_quote_request", False):
            return CollectionDecision(True, trigger=CollectionTrigger.BUDGET, reason="volunteered_contact_with_commercial")
        normalized = normalize_text(current_message)
        if any(
            marker in normalized
            for marker in (
                "preciso", "quero", "gostaria", "orcamento", "orçamento", "automacao", "automação",
                "site", "sistema", "loja", "manutencao", "manutenção", "robo", "robô",
            )
        ):
            return CollectionDecision(True, trigger=CollectionTrigger.BUDGET, reason="volunteered_contact_with_need_markers")
    return CollectionDecision(False, reason="consultative_mode")


def build_conceptual_price_reply(lead_draft=None) -> str:
    need = str(getattr(lead_draft, "need_summary", "") or "").strip()
    normalized = normalize_text(need)
    stone_context = any(
        marker in normalized
        for marker in (
            "cozinha", "bancada", "pia", "cooktop", "banheiro", "lavabo", "escada",
            "gourmet", "granito", "marmore", "mármore", "nicho", "cuba", "churrasqueira",
        )
    )
    if stone_context:
        base = (
            "O investimento varia conforme material, medidas, acabamentos e complexidade do projeto. "
            "Ainda não tenho um valor fechado para informar aqui."
        )
    else:
        base = (
            "O investimento varia conforme o escopo, volume de itens, integrações e conteúdo. "
            "Ainda não tenho um valor fechado para informar aqui."
        )
    if need:
        return f"{base} Se quiser, posso levantar os pontos do seu projeto para preparar um orçamento."
    return f"{base} Se quiser um orçamento, me diga e eu sigo com os dados necessários."


def build_consultative_commercial_reply(*, lead_draft=None, current_message: str = "", history=None) -> str:
    need = str(getattr(lead_draft, "need_summary", "") or "").strip()
    message = str(current_message or "").strip()
    if is_conceptual_price_question(message):
        return build_conceptual_price_reply(lead_draft)
    if is_direct_question(message):
        from assistant_core.conversation_turns import build_direct_question_reply, detect_question_type

        return build_direct_question_reply(
            lead_draft,
            question_type=detect_question_type(message),
            current_message=message,
        )
    if is_need_enrichment(message):
        return build_enrichment_reply(lead_draft, current_message=message)
    normalized = normalize_text(message)
    if any(token in normalized for token in ("loja virtual", "ecommerce", "e-commerce", "loja online")):
        return (
            "Entendi, uma loja virtual. Para eu te orientar melhor: "
            "você pretende vender poucos produtos no início ou já tem um catálogo maior?"
        )
    if any(token in normalized for token in ("site", "pagina", "página", "portal", "sistema web", "sistema")):
        return (
            "Claro. Posso te ajudar com isso. "
            "Qual é o objetivo principal: divulgação, captura de contatos, vendas online ou um sistema interno?"
        )
    if any(token in normalized for token in ("escola", "educac", "professor", "bncc")):
        return (
            "Entendi o contexto educacional. Para orientar melhor: "
            "o objetivo é robótica educacional, demonstração tecnológica ou outro uso na escola?"
        )
    if any(token in normalized for token in ("automacao", "automação", "robo", "robô", "robotica", "robótica")):
        return (
            "Entendi. Para te orientar melhor: qual ambiente você quer atender "
            "e qual objetivo principal (recepção, segurança, limpeza, educação ou outro)?"
        )
    # Marmoraria / pedras naturais (Pitondo e verticais similares)
    if any(token in normalized for token in ("cozinha", "bancada", "pia", "cooktop", "ilha", "frontao", "frontão")):
        return (
            "Entendi. Para te orientar melhor: o projeto é de cozinha — "
            "bancada, pia, ilha ou outro detalhe específico?"
        )
    if any(token in normalized for token in ("banheiro", "lavabo", "cuba", "nicho")):
        return (
            "Entendi o banheiro/lavabo. Você precisa de bancada, cuba, nicho "
            "ou mais de um desses itens?"
        )
    if any(token in normalized for token in ("escada", "escadas")):
        return (
            "Entendi. Para escadas, me conta se é escada completa, revestimento "
            "ou outro detalhe do projeto."
        )
    if any(token in normalized for token in ("gourmet", "churrasqueira")):
        return (
            "Entendi a área gourmet. A bancada é para apoio, pia, churrasqueira "
            "ou outro uso?"
        )
    if any(token in normalized for token in ("granito", "marmore", "mármore", "quartzito", "pedra")):
        return (
            "Entendi. Qual ambiente você quer atender com a pedra "
            "(cozinha, banheiro, escada, área gourmet ou outro)?"
        )
    if need:
        return (
            f"Entendi. Com o contexto de {_short(need)}, posso te orientar melhor. "
            "Qual detalhe é mais importante agora: material, medidas ou acabamento?"
        )
    return "Claro. Pode me contar um pouco mais sobre o que você precisa fazer ou resolver?"


def soft_merge_need_from_history(*, lead_draft, history, message: str) -> str:
    existing = str(getattr(lead_draft, "need_summary", "") or "").strip() if lead_draft is not None else ""
    merged = existing
    for item in history or []:
        if str(item.get("role") or "") != "user":
            continue
        content = str(item.get("content") or "").strip()
        if not content or is_name_deferred(content) or is_conceptual_price_question(content) or is_direct_question(content):
            continue
        if detect_collection_trigger(content) == CollectionTrigger.NONE:
            merged = merge_need_summaries(merged, content)
    if (
        message
        and not is_name_deferred(message)
        and not is_direct_question(message)
        and detect_collection_trigger(message) == CollectionTrigger.NONE
    ):
        merged = merge_need_summaries(merged, message)
    return merged[:500]


def _short(text: str) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if len(cleaned) <= 90:
        return cleaned.rstrip(".")
    return cleaned[:87].rstrip() + "…"
