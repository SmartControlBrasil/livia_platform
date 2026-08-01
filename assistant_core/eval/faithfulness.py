from __future__ import annotations

import re
from dataclasses import dataclass


FAITHFULNESS_SUPPORTED = "SUPPORTED"
FAITHFULNESS_PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
FAITHFULNESS_UNSUPPORTED = "UNSUPPORTED"
FAITHFULNESS_NO_KNOWLEDGE_REQUIRED = "NO_KNOWLEDGE_REQUIRED"


@dataclass(frozen=True)
class FaithfulnessResult:
    status: str
    matched_expected: list[str]
    matched_forbidden: list[str]
    notes: str = ""


def classify_faithfulness(
    reply: str,
    *,
    facts_expected: list[str] | None = None,
    facts_forbidden: list[str] | None = None,
    require_knowledge: bool = True,
    allow_partial_ok: bool = False,
) -> FaithfulnessResult:
    text = str(reply or "").strip().lower()
    expected = [str(item).strip().lower() for item in (facts_expected or []) if str(item).strip()]
    forbidden = [str(item).strip().lower() for item in (facts_forbidden or []) if str(item).strip()]

    if not require_knowledge:
        matched_forbidden = _match_forbidden(text, forbidden)
        if matched_forbidden:
            return FaithfulnessResult(
                status=FAITHFULNESS_UNSUPPORTED,
                matched_expected=[],
                matched_forbidden=matched_forbidden,
                notes="forbidden_fact_in_no_knowledge_case",
            )
        return FaithfulnessResult(
            status=FAITHFULNESS_NO_KNOWLEDGE_REQUIRED,
            matched_expected=[],
            matched_forbidden=[],
        )

    matched_forbidden = _match_forbidden(text, forbidden)
    matched_expected = [item for item in expected if item in text]

    if matched_forbidden and not matched_expected:
        return FaithfulnessResult(
            status=FAITHFULNESS_UNSUPPORTED,
            matched_expected=matched_expected,
            matched_forbidden=matched_forbidden,
            notes="forbidden_without_support",
        )

    if expected:
        if len(matched_expected) == len(expected) and not matched_forbidden:
            return FaithfulnessResult(
                status=FAITHFULNESS_SUPPORTED,
                matched_expected=matched_expected,
                matched_forbidden=matched_forbidden,
            )
        if matched_expected and not matched_forbidden:
            status = FAITHFULNESS_PARTIALLY_SUPPORTED
            if allow_partial_ok and _looks_like_partial_evidence_reply(text):
                status = FAITHFULNESS_SUPPORTED
            return FaithfulnessResult(
                status=status,
                matched_expected=matched_expected,
                matched_forbidden=matched_forbidden,
                notes="partial_expected_coverage",
            )
        if matched_forbidden:
            return FaithfulnessResult(
                status=FAITHFULNESS_UNSUPPORTED,
                matched_expected=matched_expected,
                matched_forbidden=matched_forbidden,
                notes="forbidden_with_partial_expected",
            )
        return FaithfulnessResult(
            status=FAITHFULNESS_UNSUPPORTED,
            matched_expected=matched_expected,
            matched_forbidden=matched_forbidden,
            notes="missing_expected_facts",
        )

    if matched_forbidden:
        return FaithfulnessResult(
            status=FAITHFULNESS_UNSUPPORTED,
            matched_expected=[],
            matched_forbidden=matched_forbidden,
        )

    if not text:
        return FaithfulnessResult(
            status=FAITHFULNESS_UNSUPPORTED,
            matched_expected=[],
            matched_forbidden=[],
            notes="empty_reply_requires_knowledge",
        )

    return FaithfulnessResult(
        status=FAITHFULNESS_PARTIALLY_SUPPORTED,
        matched_expected=[],
        matched_forbidden=[],
        notes="no_expected_facts_defined",
    )


def _match_forbidden(text: str, forbidden: list[str]) -> list[str]:
    matched: list[str] = []
    for item in forbidden:
        if item not in text:
            continue
        if _is_negated_mention(text, item):
            continue
        if _is_user_echo_only(text, item):
            continue
        matched.append(item)
    return matched


def _is_negated_mention(text: str, term: str) -> bool:
    for match in re.finditer(re.escape(term), text):
        start = match.start()
        window = text[max(0, start - 48) : start]
        negations = (
            "não ",
            "nao ",
            "nunca ",
            "sem ",
            "não posso ",
            "nao posso ",
            "não revel",
            "nao revel",
            "não envio ",
            "nao envio ",
            "não tenho ",
            "nao tenho ",
            "não encontrei ",
            "nao encontrei ",
            "não há informação",
            "nao ha informacao",
            "não afirm",
            "nao afirm",
        )
        if any(token in window for token in negations):
            return True
    return False


_PARTIAL_TIMELINE_ECHO_TERMS = frozenset(
    {
        "48 horas",
        "48h",
        "instalação em 48",
        "instalacao em 48",
        "execução em 48",
        "execucao em 48",
        "fica pronta em 48",
        "entregamos em 48",
    }
)


def _is_user_echo_only(text: str, term: str) -> bool:
    """Eco da pergunta do usuário sobre prazo/execução, sem afirmação empresarial."""
    if term not in _PARTIAL_TIMELINE_ECHO_TERMS:
        return False
    safe_markers = (
        "não encontrei",
        "nao encontrei",
        "não há informação",
        "nao ha informacao",
        "informação disponível indica",
        "informacao disponivel indica",
        "sua pergunta",
        "pergunta de",
    )
    if any(marker in text for marker in safe_markers):
        return True
    affirmations = (
        "entregamos em",
        "instalamos em",
        "fica pronto em",
        "ficará pront",
        "ficara pront",
        "execução em",
        "execucao em",
        "obras em",
        "prazo de instala",
        "prazo de execu",
    )
    return not any(marker in text for marker in affirmations)


def _looks_like_partial_evidence_reply(text: str) -> bool:
    markers = (
        "não há informação",
        "nao ha informacao",
        "não encontrei",
        "nao encontrei",
        "informação disponível indica",
        "informacao disponivel indica",
        "somente",
        "apenas",
        "na base disponível",
        "na base disponivel",
    )
    return any(marker in text for marker in markers)


def contains_wrong_vertical(reply: str, *, forbidden_vertical_terms: list[str] | None = None) -> bool:
    terms = forbidden_vertical_terms or ["automação industrial", "automacao industrial", "smart control"]
    lowered = str(reply or "").lower()
    return any(term in lowered for term in terms)
