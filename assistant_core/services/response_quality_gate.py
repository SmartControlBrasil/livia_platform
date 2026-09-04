"""Quality gate determinístico antes de devolver resposta ao usuário."""

from __future__ import annotations

import re

from assistant_core.conversation_turns import normalize_text
from assistant_core.dialogue_memory import DialogueMemory, should_skip_consultative_followup
from assistant_core.services.deterministic_synthesis import (
    is_generic_fallback_reply,
    strip_meta_rag_phrasing,
    synthesize_deterministic_reply,
)
from assistant_core.followup_strategy import select_followup
from knowledge_base.rag.content_classification import is_policy_leak_text


ACKNOWLEDGEMENT_ONLY_PHRASES = (
    "entendi",
    "entendi isso ajuda a detalhar a necessidade",
    "certo",
    "perfeito",
    "ok",
)


def is_acknowledgement_only_reply(text: str) -> bool:
    """True quando a resposta é só confirmação genérica, sem conteúdo consultivo."""
    cleaned = normalize_text(str(text or "").strip())
    if not cleaned:
        return True
    if any(token in cleaned for token in ("hygibot", "dune", "duno", "limpeza", "lavar", "varrer", "aspirar", "fluxo", "documentação", "documentacao", "confirmação", "confirmacao")):
        return False
    if "?" in str(text or ""):
        # Pergunta técnica isolada ainda conta como conteúdo útil.
        if len(cleaned) > len(ACKNOWLEDGEMENT_ONLY_PHRASES[1]) + 8:
            return False
    stripped = normalize_text(re.sub(r"[^\w\s]", " ", cleaned))
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if stripped in ACKNOWLEDGEMENT_ONLY_PHRASES:
        return True
    for phrase in ACKNOWLEDGEMENT_ONLY_PHRASES:
        if stripped == phrase or stripped.startswith(f"{phrase} "):
            remainder = stripped[len(phrase) :].strip()
            if not remainder or remainder in ACKNOWLEDGEMENT_ONLY_PHRASES:
                return True
    return False


def has_substantive_consultative_content(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned or is_generic_fallback_reply(cleaned) or is_acknowledgement_only_reply(cleaned):
        return False
    return len(normalize_text(cleaned)) >= 20


CROSS_DOMAIN_MARKERS = {
    "robotics": (
        "python",
        "loja virtual",
        "ecommerce",
        "mitsubishi",
        "clp",
    ),
    "automation": (
        "python",
        "loja virtual",
        "ecommerce",
        "hygibot",
        "duno",
        "dune",
        "limpeza",
        "escola",
        "liro",
        "robotica de servico",
        "robótica de serviço",
        "xyron",
    ),
    "materials": ("python", "mitsubishi", "hygibot", "duno", "dune", "loja virtual"),
    "software_web": ("granito", "bancada", "hygibot", "duno", "limpeza", "mitsubishi"),
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
        "followup_selected": False,
        "followup_domain": memory.active_domain or "",
        "policy_leak_blocked": False,
        "regrounded": False,
        "meta_rag_stripped": False,
        "answer_shape": "",
        "active_application": getattr(memory, "active_application", "") or "",
    }
    ambiguity_options = list((getattr(memory, "notes", {}) or {}).get("entity_ambiguity_options") or [])
    if ambiguity_options:
        options = ", ".join(str(item) for item in ambiguity_options[:5] if str(item).strip())
        if options:
            return f"Você está se referindo a qual modelo: {options}?", {**diagnostics, "followup_strategy": "entity_disambiguation"}

    text = strip_meta_rag_phrasing(str(reply or "").strip())
    if text != str(reply or "").strip():
        diagnostics["meta_rag_stripped"] = True

    # A) Policy leak
    if is_policy_leak_text(text):
        diagnostics["policy_leak_blocked"] = True
        regenerated = synthesize_deterministic_reply(
            knowledge_context,
            base_reply="",
            current_message=current_message,
            active_domain=memory.active_domain,
            active_application=getattr(memory, "active_application", "") or "",
        )
        if regenerated and not is_policy_leak_text(regenerated):
            text = regenerated
            diagnostics["regrounded"] = True
        else:
            text = _safe_entity_fallback(memory, current_message)

    # B/D) Cross-domain noise
    cleaned, removed = _strip_incompatible_sentences(
        text, memory.active_domain, memory.active_topic, getattr(memory, "active_application", "") or ""
    )
    diagnostics["coherence_filtered_count"] = removed
    text = cleaned or text

    # C) Entity mentioned but missing from reply → try re-synthesize from KB
    # Não sobrescrever resposta de preço conceitual / policy comercial.
    if _is_commercial_policy_reply(text):
        diagnostics["regrounded"] = False
    elif memory.active_entity and memory.active_entity.lower() not in normalize_text(text):
        if detect_entity_in_message(current_message) or "fale sobre" in normalize_text(current_message):
            regenerated = synthesize_deterministic_reply(
                knowledge_context,
                base_reply="",
                current_message=current_message,
                active_domain=memory.active_domain,
                active_application=getattr(memory, "active_application", "") or "",
            )
            if regenerated and not is_policy_leak_text(regenerated):
                if memory.active_entity.lower() in normalize_text(regenerated) or _looks_like_cleaning_robot(regenerated):
                    text = regenerated
                    diagnostics["regrounded"] = True

    # E) Fallback despite evidence
    if is_generic_fallback_reply(text) and knowledge_context.strip():
        regenerated = synthesize_deterministic_reply(
            knowledge_context,
            base_reply="",
            current_message=current_message,
            active_domain=memory.active_domain,
            active_application=getattr(memory, "active_application", "") or "",
        )
        if regenerated and not is_policy_leak_text(regenerated):
            text = regenerated
            diagnostics["regrounded"] = True

    text = strip_meta_rag_phrasing(text)

    # Follow-up strategy centralizada
    if append_followup is False or should_skip_consultative_followup(current_message=current_message, memory=memory):
        diagnostics["followup_strategy"] = "skipped_direct_ask"
        text = _strip_known_bad_followups(text, active_domain=memory.active_domain, active_topic=memory.active_topic, active_application=getattr(memory, "active_application", "") or "")
    elif append_followup is True:
        follow, follow_diag = select_followup(
            memory=memory,
            current_message=current_message,
            need_summary=need_summary,
            answer_text=text,
            force=True,
        )
        diagnostics.update(follow_diag)
        if follow:
            text = f"{text} {follow}".strip()
        else:
            text = _strip_known_bad_followups(text, active_domain=memory.active_domain, active_topic=memory.active_topic, active_application=getattr(memory, "active_application", "") or "")
    else:
        # Default: sanitiza follow-ups incompatíveis já presentes; não inventa pergunta nova.
        text = _strip_known_bad_followups(text, active_domain=memory.active_domain, active_topic=memory.active_topic, active_application=getattr(memory, "active_application", "") or "")
        diagnostics["followup_strategy"] = "sanitize_existing"

    if is_policy_leak_text(text):
        diagnostics["policy_leak_blocked"] = True
        text = _safe_entity_fallback(memory, current_message)

    if is_acknowledgement_only_reply(text) and knowledge_context.strip():
        regenerated = synthesize_deterministic_reply(
            knowledge_context,
            base_reply="",
            current_message=_consultative_synthesis_query(current_message, need_summary, memory),
            active_domain=memory.active_domain,
            active_application=getattr(memory, "active_application", "") or "",
        )
        follow, follow_diag = select_followup(
            memory=memory,
            current_message=current_message,
            need_summary=need_summary,
            answer_text=regenerated,
            history=history,
            force=False,
        )
        diagnostics.update(follow_diag)
        parts = [part for part in (regenerated, follow) if part and str(part).strip()]
        if parts:
            text = " ".join(parts).strip()
            diagnostics["regrounded"] = True
            diagnostics["followup_strategy"] = follow_diag.get("followup_strategy", diagnostics["followup_strategy"])

    return strip_meta_rag_phrasing(text).strip(), diagnostics


def _consultative_synthesis_query(current_message: str, need_summary: str, memory: DialogueMemory) -> str:
    parts = [str(current_message or "").strip()]
    need = str(need_summary or getattr(memory, "active_need", "") or "").strip()
    if need and normalize_text(need) not in normalize_text(" ".join(parts)):
        parts.append(need)
    blob = normalize_text(" ".join(parts))
    if getattr(memory, "active_application", "") == "cleaning_robotics" or getattr(memory, "active_topic", "") == "cleaning_robot":
        if not any(token in blob for token in ("hygibot", "dune", "duno", "limpeza profissional")):
            entity = str(getattr(memory, "active_entity", "") or "").strip()
            if entity:
                parts.append(entity)
            parts.append("limpeza profissional hygibot")
    return " ".join(part for part in parts if part)[:500]


def detect_entity_in_message(message: str) -> bool:
    from assistant_core.dialogue_memory import detect_entity_mention

    return detect_entity_mention(message) is not None


def _is_commercial_policy_reply(text: str) -> bool:
    normalized = normalize_text(text)
    return any(
        token in normalized
        for token in (
            "investimento varia",
            "valor fechado",
            "nao tenho um valor",
            "ainda nao tenho um valor",
        )
    )


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


def _strip_incompatible_sentences(
    text: str,
    active_domain: str,
    active_topic: str = "",
    active_application: str = "",
) -> tuple[str, int]:
    if not text:
        return "", 0
    markers = list(CROSS_DOMAIN_MARKERS.get(active_domain or "", ()))
    if active_topic == "cleaning_robot" or active_application == "cleaning_robotics":
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
                "mitsubishi",
            )
        )
    if active_topic == "educational_robot" or active_application == "educational_robotics":
        markers.extend(("limpeza", "duno", "dune", "hygibot", "mitsubishi", "piso"))
    if active_domain == "automation" or active_application == "industrial_automation":
        markers.extend(("robotica de servico", "robótica de serviço", "limpeza", "escola", "xyron"))
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


def _strip_known_bad_followups(
    text: str,
    *,
    active_domain: str = "",
    active_topic: str = "",
    active_application: str = "",
) -> str:
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
        if active_topic == "cleaning_robot" or active_application == "cleaning_robotics":
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
        if active_topic == "educational_robot" or active_application == "educational_robotics":
            cleaned = re.sub(
                r"(?i)\s*Entendi, isso ajuda a detalhar a necessidade\. Qual é o ambiente e o tipo de piso onde a limpeza acontece\?\s*",
                " ",
                cleaned,
            )
            cleaned = re.sub(
                r"(?i)\s*Qual é o ambiente e o tipo de piso onde a limpeza acontece\?\s*",
                " ",
                cleaned,
            )
            cleaned = re.sub(
                r"(?i)\s*Entendi, isso ajuda a detalhar a necessidade\.\s*$",
                " ",
                cleaned,
            )
    if active_domain == "automation":
        cleaned = re.sub(
            r"(?i)\s*Trabalhamos com rob[oó]tica de servi[cç]o[^.]*\.\s*",
            " ",
            cleaned,
        )
        cleaned = re.sub(
            r"(?i)\s*Se quiser, me conta o ambiente e o objetivo principal[^.]*\.\s*",
            " ",
            cleaned,
        )
    return re.sub(r"\s{2,}", " ", cleaned).strip()
