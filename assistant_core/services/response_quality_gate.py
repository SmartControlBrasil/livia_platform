"""Quality gate determinístico antes de devolver resposta ao usuário."""

from __future__ import annotations

import re

from assistant_core.conversation_turns import normalize_text
from assistant_core.dialogue_memory import DialogueMemory, should_skip_consultative_followup
from assistant_core.services.deterministic_synthesis import (
    consultative_followup_for_context,
    is_generic_fallback_reply,
    synthesize_deterministic_reply,
)
from knowledge_base.rag.content_classification import is_policy_leak_text


CROSS_DOMAIN_MARKERS = {
    "robotics": (
        "python",
        "loja virtual",
        "ecommerce",
        "criancas e jovens",
        "crianças e jovens",
        "centro universitario",
        "experiencias educacionais",
        "experiências educacionais",
        "aproximar criancas",
        "aproximar crianças",
    ),
    "materials": ("python", "mitsubishi", "hygibot", "duno", "dune", "loja virtual"),
    "software_web": ("granito", "bancada", "hygibot", "duno", "limpeza"),
}


def apply_response_quality_gate(
    *,
    reply: str,
    knowledge_context: str = "",
    current_message: str = "",
    memory: DialogueMemory | None = None,
    need_summary: str = "",
    history=None,
    append_followup: bool | None = None,
) -> tuple[str, dict]:
    """
    Valida/repara resposta determinística.

    Retorna (reply_final, diagnostics).
    """
    memory = memory or DialogueMemory()
    diagnostics = {
        "policy_chunk_filtered": False,
        "coherence_filtered_count": 0,
        "followup_strategy": "none",
        "policy_leak_blocked": False,
        "regrounded": False,
    }
    text = str(reply or "").strip()

    # A) Policy leak
    if is_policy_leak_text(text):
        diagnostics["policy_leak_blocked"] = True
        regenerated = synthesize_deterministic_reply(knowledge_context, base_reply="")
        if regenerated and not is_policy_leak_text(regenerated):
            text = regenerated
            diagnostics["regrounded"] = True
        else:
            text = _safe_entity_fallback(memory, current_message)

    # B/D) Cross-domain noise
    cleaned, removed = _strip_incompatible_sentences(text, memory.active_domain, memory.active_topic)
    diagnostics["coherence_filtered_count"] = removed
    text = cleaned or text

    # C) Entity mentioned but missing from reply → try re-synthesize from KB
    if memory.active_entity and memory.active_entity.lower() not in normalize_text(text):
        if detect_entity_in_message(current_message) or "fale sobre" in normalize_text(current_message):
            regenerated = synthesize_deterministic_reply(knowledge_context, base_reply="")
            if regenerated and not is_policy_leak_text(regenerated):
                if memory.active_entity.lower() in normalize_text(regenerated) or _looks_like_cleaning_robot(regenerated):
                    text = regenerated
                    diagnostics["regrounded"] = True

    # E) Fallback despite evidence
    if is_generic_fallback_reply(text) and knowledge_context.strip():
        regenerated = synthesize_deterministic_reply(knowledge_context, base_reply="")
        if regenerated and not is_policy_leak_text(regenerated):
            text = regenerated
            diagnostics["regrounded"] = True

    # Follow-up strategy
    skip = should_skip_consultative_followup(current_message=current_message, memory=memory)
    if append_followup is False or skip:
        diagnostics["followup_strategy"] = "skipped_direct_ask"
        text = _strip_known_bad_followups(text)
    elif append_followup is True:
        follow = consultative_followup_for_context(
            " ".join([memory.active_domain, memory.active_entity, need_summary, current_message])
        )
        if follow and follow.lower() not in text.lower():
            # Nunca anexar follow-up de ecommerce em robotics.
            if memory.active_domain == "robotics" and "catálogo" in follow.lower():
                diagnostics["followup_strategy"] = "blocked_cross_domain_followup"
            else:
                text = f"{text} {follow}".strip()
                diagnostics["followup_strategy"] = "domain_followup"
    else:
        # Default: se já veio follow-up ruim, remove.
        text = _strip_known_bad_followups(text, active_domain=memory.active_domain)
        diagnostics["followup_strategy"] = "sanitize_existing"

    if is_policy_leak_text(text):
        diagnostics["policy_leak_blocked"] = True
        text = _safe_entity_fallback(memory, current_message)

    return text.strip(), diagnostics


def detect_entity_in_message(message: str) -> bool:
    from assistant_core.dialogue_memory import detect_entity_mention

    return detect_entity_mention(message) is not None


def _looks_like_cleaning_robot(text: str) -> bool:
    lowered = normalize_text(text)
    return any(token in lowered for token in ("limpeza", "lavar", "varrer", "aspirar", "passar pano", "grandes areas", "grandes áreas"))


def _safe_entity_fallback(memory: DialogueMemory, current_message: str) -> str:
    if memory.active_entity and memory.active_topic == "cleaning_robot":
        return (
            f"O {memory.active_entity} é uma solução voltada à limpeza de áreas amplas. "
            "Conforme a configuração e a aplicação, ele pode apoiar rotinas de varrição, "
            "aspiração, lavagem e passagem de pano. Para indicar se ele se encaixa bem no seu caso, "
            "o mais importante é entender o ambiente, o tipo de piso e a circulação de pessoas."
        )
    if memory.active_domain == "robotics":
        return (
            "Posso te orientar sobre robótica de serviço com base no que temos documentado. "
            "Me diga qual modelo ou aplicação você quer detalhar."
        )
    return (
        "Posso continuar te orientando com as informações disponíveis. "
        "Qual ponto você quer esclarecer primeiro?"
    )


def _strip_incompatible_sentences(text: str, active_domain: str, active_topic: str = "") -> tuple[str, int]:
    if not text:
        return "", 0
    markers = list(CROSS_DOMAIN_MARKERS.get(active_domain or "", ()))
    if active_topic == "cleaning_robot" or active_domain == "robotics":
        markers.extend(
            (
                "criancas e jovens",
                "crianças e jovens",
                "experiencias educacionais",
                "experiências educacionais",
                "robô educacional",
                "robo educacional",
                "little bot",
                "liro",
            )
        )
    if not markers:
        return text, 0
    kept: list[str] = []
    removed = 0
    for part in re.split(r"(?<=[.!?])\s+", text):
        lowered = normalize_text(part)
        if any(marker in lowered for marker in markers):
            removed += 1
            continue
        kept.append(part.strip())
    return " ".join(kept).strip(), removed


def _strip_known_bad_followups(text: str, *, active_domain: str = "") -> str:
    bad = (
        "Você pretende começar com poucos produtos ou já tem um catálogo maior?",
        "voce pretende comecar com poucos produtos ou ja tem um catalogo maior?",
    )
    cleaned = text
    for phrase in bad:
        cleaned = re.sub(re.escape(phrase), "", cleaned, flags=re.IGNORECASE).strip()
    if active_domain == "robotics":
        cleaned = re.sub(
            r"\s*Desenvolvemos sistemas, integrações, IoT e soluções digitais em Python\.\s*",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\s*Robô interativo para aproximar crianças e jovens da tecnologia[^.]*\.\s*",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\s*Robo interativo para aproximar criancas e jovens da tecnologia[^.]*\.\s*",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
    return re.sub(r"\s{2,}", " ", cleaned).strip()
