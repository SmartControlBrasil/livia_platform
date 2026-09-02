from __future__ import annotations

from dataclasses import dataclass

from assistant_core.state import LeadState, get_current_state


@dataclass(frozen=True)
class DecisionOutcome:
    """Separa decisão operacional (state machine) de síntese de linguagem."""

    kind: str
    allow_knowledge_synthesis: bool
    skip_reason: str = ""
    synthesis_mode: str = "inform"
    evidence_sufficiency: str = "sufficient"
    evidence_reason: str = ""


def is_informational_knowledge_query(discovery) -> bool:
    normalized = str(getattr(discovery, "normalized_text", "") or "")
    if bool(getattr(discovery, "has_quote_request", False)):
        timeline_markers = ("prazo", "entrega", "tempo de", "quanto tempo", "demora")
        if any(marker in normalized for marker in timeline_markers):
            return True
        return False
    informational_markers = (
        "quais ",
        "como ",
        "posso ",
        "serve ",
        "tem ",
        "trabalham",
        "fazem",
        "voces vendem",
        "vocês vendem",
        "limpar",
        "cuidado",
        "entregam",
        "medicao",
        "medição",
        "indicado",
        "robotica educacional",
        "robótica educacional",
        "robo de",
        "robô de",
        "robo para",
        "robô para",
        "clp",
        "ihm",
        "mitsubishi",
        "python",
    )
    if any(marker in normalized for marker in informational_markers):
        return True
    return "?" in normalized and not bool(getattr(discovery, "should_collect_lead", False))


def is_ambiguous_product_query(discovery) -> bool:
    """Consulta comercial vaga sem objeto ou aplicação clara."""
    if bool(getattr(discovery, "has_quote_request", False)):
        return False
    normalized = str(getattr(discovery, "normalized_text", "") or "")
    if not normalized:
        return False
    vague_markers = (
        "algo",
        "alguma coisa",
        "uma coisa",
        "opcao",
        "opção",
        "solucao",
        "solução",
        "produto",
        "servico",
        "serviço",
    )
    if any(marker in normalized for marker in vague_markers):
        return True
    meaningful = [word for word in normalized.split() if len(word) > 3]
    return bool(getattr(discovery, "should_ask_discovery_question", False)) and len(meaningful) <= 3


def has_concrete_quote_specs(discovery) -> bool:
    normalized = str(getattr(discovery, "normalized_text", "") or "")
    spec_markers = (
        "metro",
        "metragem",
        "medida",
        " x ",
        "cm",
        "mm",
        "1m",
        "2m",
        "3m",
        "4m",
        "5m",
    )
    if any(marker in normalized for marker in spec_markers):
        return True
    return any(part.isdigit() and len(part) <= 2 for part in normalized.split())


def should_combine_kb_with_discovery(discovery, conversation, knowledge_context: str) -> bool:
    if not _has_semantic_knowledge(knowledge_context):
        return False
    if is_informational_knowledge_query(discovery):
        return False
    lead_state = get_current_state(conversation) if conversation is not None else LeadState.DISCOVERY
    if lead_state not in {LeadState.DISCOVERY, LeadState.COLLECT_NEED}:
        return False
    if has_concrete_quote_specs(discovery):
        return False
    intent = str(getattr(discovery, "intent", "") or "")
    if intent in {"quote_request", "commercial_interest"}:
        return True
    return is_commercial_discovery_with_knowledge(discovery)


def is_commercial_discovery_with_knowledge(discovery) -> bool:
    """Intenção comercial inicial ainda precisa de discovery antes de redigir com KB."""
    if not bool(getattr(discovery, "should_ask_discovery_question", False)):
        return False
    normalized = str(getattr(discovery, "normalized_text", "") or "")
    action_markers = ("quero ", "preciso ", "gostaria", "contratar", "comprar", "fazer", "criar")
    return any(marker in normalized for marker in action_markers)


def resolve_decision_outcome(*, decision, discovery, conversation, knowledge_context: str = "") -> DecisionOutcome:
    if getattr(decision, "handoff_request_id", None):
        return DecisionOutcome("handoff", False, skip_reason="handoff_active")

    intent = str(getattr(decision, "intent", "") or "")
    if intent == "greeting":
        return DecisionOutcome("greeting", False, skip_reason="greeting")

    has_knowledge = _has_semantic_knowledge(knowledge_context)

    if has_knowledge and is_ambiguous_product_query(discovery):
        return DecisionOutcome("clarify", True, synthesis_mode="clarify")

    if should_combine_kb_with_discovery(discovery, conversation, knowledge_context):
        return DecisionOutcome("discovery", True, synthesis_mode="combine_discovery")

    if bool(getattr(discovery, "should_collect_lead", False)) and not is_informational_knowledge_query(discovery):
        return DecisionOutcome("qualification", False, skip_reason="collect_lead")

    lead_state = get_current_state(conversation) if conversation is not None else LeadState.DISCOVERY
    if lead_state in {LeadState.COLLECT_NAME_COMPANY, LeadState.COLLECT_CONTACT}:
        return DecisionOutcome("qualification", False, skip_reason=f"lead_state_{lead_state}")

    if not has_knowledge:
        return DecisionOutcome("empty", False, skip_reason="no_knowledge_context")

    # Pergunta informativa (prazo, como funciona, etc.) responde com KB — sem forçar discovery.
    if is_informational_knowledge_query(discovery):
        return DecisionOutcome("inform", True, synthesis_mode="inform")

    if bool(getattr(discovery, "should_ask_discovery_question", False)):
        category = str(getattr(discovery, "category", "") or getattr(discovery, "scenario", "") or "")
        if category == "ambígua" or "ambig" in category.lower():
            return DecisionOutcome("clarify", True, synthesis_mode="clarify")
        if is_commercial_discovery_with_knowledge(discovery):
            return DecisionOutcome("discovery", True, synthesis_mode="combine_discovery")
        if intent in {"quote_request", "commercial_interest"} and not getattr(discovery, "should_answer_contextually", False):
            return DecisionOutcome("discovery", True, synthesis_mode="combine_discovery")
        if intent == "unknown" and is_commercial_discovery_with_knowledge(discovery):
            return DecisionOutcome("discovery", True, synthesis_mode="combine_discovery")

    return DecisionOutcome("inform", True, synthesis_mode="inform")


def _has_semantic_knowledge(knowledge_context: str) -> bool:
    text = str(knowledge_context or "").strip()
    if not text or "[KNOWLEDGE_BASE]" not in text.upper():
        return False
    return bool(text)


def has_semantic_knowledge_block(knowledge_context: str) -> bool:
    return _has_semantic_knowledge(knowledge_context)
