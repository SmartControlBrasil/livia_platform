from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class EvidenceSufficiency(str, Enum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class EvidenceAssessment:
    status: EvidenceSufficiency
    reason: str = ""
    category: str = ""
    retrieval_score: float = 0.0
    chunk_ids: tuple[int, ...] = ()


QUOTE_TIMELINE_MARKERS = (
    "orcamento",
    "orçamento",
    "retorno do orcamento",
    "retorno do orçamento",
    "retornar o orcamento",
    "retornar o orçamento",
    "proposta",
    "envio das informacoes",
    "envio das informações",
)

EXECUTION_TIMELINE_MARKERS = (
    "instalacao",
    "instalação",
    "execucao",
    "execução",
    "obra",
    "montagem",
    "fica pronto",
    "ficara pronta",
    "ficará pronta",
    "entrega do projeto",
    "pronto em",
    "conclusao",
    "conclusão",
)

TIMELINE_QUESTION_MARKERS = (
    "prazo",
    "tempo",
    "quanto tempo",
    "em quanto tempo",
    "demora",
    "48 horas",
    "48h",
    "em quanto",
)

NEGATION_ABSENCE_MARKERS = (
    "garantia",
    "warranty",
)

REGION_QUESTION_MARKERS = (
    "atendem",
    "atende",
    "fazem entrega",
    "entregam em",
    "regiao",
    "região",
    "cidade",
)

TECHNICAL_REQUIREMENT_TERMS = (
    "bncc",
    "certificado",
    "certificacao",
    "certificação",
    "nasa",
    "autonomia",
    "bateria",
    "recarga",
    "carrega",
    "carregar",
    "tomada",
    "sensor",
    "sensores",
    "capacidade",
    "tensao",
    "tensão",
    "voltagem",
    "garantia",
    "epoxi",
    "epóxi",
    "porcelanato",
    "ip67",
    "peso",
    "pesa",
)

PRODUCT_OR_APPLICATION_TERMS = (
    "liro",
    "little bot",
    "littlebot",
    "duno",
    "dune",
    "hygibot",
    "orbit",
    "patrol",
    "neobot",
    "buddy",
    "xyron",
    "educacional",
    "escola",
    "limpeza",
    "patrulhamento",
)


def assess_evidence_sufficiency(
    *,
    message: str,
    knowledge_context: str,
    max_score: float = 0.0,
    chunk_ids: list[int] | None = None,
) -> EvidenceAssessment:
    """
    Avalia se a KB recuperada sustenta a afirmação implícita na pergunta.

    Determinístico, reutilizável por tenant, sem LLM.
    """
    kb_text = _extract_kb_content(knowledge_context)
    if not kb_text:
        return EvidenceAssessment(
            status=EvidenceSufficiency.INSUFFICIENT,
            reason="no_semantic_knowledge",
            category="missing_context",
            retrieval_score=max_score,
            chunk_ids=tuple(chunk_ids or ()),
        )

    msg = _normalize(message)
    kb = _normalize(kb_text)
    ids = tuple(chunk_ids or ())

    quote_timeline = _mentions_quote_timeline(kb)
    execution_timeline = _mentions_execution_timeline(kb)
    query_quote = _asks_quote_timeline(msg)
    query_execution = _asks_execution_timeline(msg)

    if query_execution and quote_timeline and not execution_timeline:
        return EvidenceAssessment(
            status=EvidenceSufficiency.PARTIAL,
            reason="kb_covers_quote_timeline_not_execution",
            category="qualifier_mismatch:quote_vs_execution",
            retrieval_score=max_score,
            chunk_ids=ids,
        )

    if query_quote and quote_timeline and not query_execution:
        return EvidenceAssessment(
            status=EvidenceSufficiency.SUFFICIENT,
            reason="quote_timeline_supported",
            category="timeline_quote",
            retrieval_score=max_score,
            chunk_ids=ids,
        )

    if query_execution and execution_timeline:
        return EvidenceAssessment(
            status=EvidenceSufficiency.SUFFICIENT,
            reason="execution_timeline_supported",
            category="timeline_execution",
            retrieval_score=max_score,
            chunk_ids=ids,
        )

    if _asks_unsupported_negation(msg, kb):
        return EvidenceAssessment(
            status=EvidenceSufficiency.INSUFFICIENT,
            reason="topic_not_in_knowledge_base",
            category="missing_topic",
            retrieval_score=max_score,
            chunk_ids=ids,
        )

    requirement_gap = _technical_requirement_gap(msg, kb)
    if requirement_gap:
        status = (
            EvidenceSufficiency.INSUFFICIENT
            if "nasa" in requirement_gap
            else EvidenceSufficiency.PARTIAL if _has_product_or_application_overlap(msg, kb) else EvidenceSufficiency.INSUFFICIENT
        )
        return EvidenceAssessment(
            status=status,
            reason=requirement_gap,
            category="missing_technical_requirement",
            retrieval_score=max_score,
            chunk_ids=ids,
        )

    region_mismatch = _region_qualifier_mismatch(msg, kb)
    if region_mismatch:
        return EvidenceAssessment(
            status=EvidenceSufficiency.PARTIAL,
            reason=region_mismatch,
            category="qualifier_mismatch:region",
            retrieval_score=max_score,
            chunk_ids=ids,
        )

    numeric_mismatch = _numeric_qualifier_mismatch(msg, kb)
    if numeric_mismatch:
        return EvidenceAssessment(
            status=EvidenceSufficiency.PARTIAL,
            reason=numeric_mismatch,
            category="qualifier_mismatch:numeric",
            retrieval_score=max_score,
            chunk_ids=ids,
        )

    return EvidenceAssessment(
        status=EvidenceSufficiency.SUFFICIENT,
        reason="default_supported",
        category="general",
        retrieval_score=max_score,
        chunk_ids=ids,
    )


def parse_chunk_ids_from_context(knowledge_context: str) -> list[int]:
    ids: list[int] = []
    for line in str(knowledge_context or "").splitlines():
        stripped = line.strip()
        match = re.match(r"^chunk:(\d+)$", stripped, flags=re.IGNORECASE)
        if match:
            ids.append(int(match.group(1)))
        ref_match = re.search(r"chunk[:/](\d+)", stripped, flags=re.IGNORECASE)
        if ref_match:
            ids.append(int(ref_match.group(1)))
    return sorted(set(ids))


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.lower()).strip()


def _extract_kb_content(knowledge_context: str) -> str:
    text = str(knowledge_context or "")
    if "[KNOWLEDGE_BASE]" not in text.upper():
        return ""
    parts: list[str] = []
    capture = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        upper = line.upper()
        if upper.startswith("[KNOWLEDGE_BASE]") or upper.startswith("[/KNOWLEDGE_BASE]"):
            capture = False
            continue
        if line.lower().startswith("conteúdo:") or line.lower().startswith("conteudo:"):
            capture = True
            continue
        if line.lower().startswith(("fonte:", "referência:", "referencia:", "score:")):
            capture = False
            continue
        if capture and line:
            parts.append(line)
    if parts:
        return "\n".join(parts)
    return text


def _mentions_quote_timeline(text: str) -> bool:
    return any(marker in text for marker in QUOTE_TIMELINE_MARKERS)


def _mentions_execution_timeline(text: str) -> bool:
    execution_phrases = (
        "instalação em",
        "instalacao em",
        "execução em",
        "execucao em",
        "entrega em",
        "obra em",
        "fica pronta em",
        "ficara pronta em",
        "ficará pronta em",
        "prazo de instala",
        "prazo de execu",
    )
    if not any(phrase in text for phrase in execution_phrases):
        return False
    return any(marker in text for marker in ("48 horas", "48h", "prazo", "tempo", "dias", "horas"))


def _asks_quote_timeline(text: str) -> bool:
    has_timeline = any(marker in text for marker in TIMELINE_QUESTION_MARKERS)
    has_quote = any(marker in text for marker in QUOTE_TIMELINE_MARKERS)
    return has_quote and has_timeline


def _asks_execution_timeline(text: str) -> bool:
    if any(marker in text for marker in EXECUTION_TIMELINE_MARKERS):
        return True
    has_timeline = any(marker in text for marker in TIMELINE_QUESTION_MARKERS)
    if not has_timeline:
        return False
    project_markers = ("projeto", "cozinha", "banheiro", "instala", "obra", "fica pront", "entrega")
    return any(marker in text for marker in project_markers)


def _asks_unsupported_negation(message: str, kb: str) -> bool:
    """Pergunta sobre tópico ausente na KB — não autoriza negação positiva."""
    if not any(marker in message for marker in NEGATION_ABSENCE_MARKERS):
        return False
    if "garantia" in message and "garantia" not in kb:
        return True
    return False


def _region_qualifier_mismatch(message: str, kb: str) -> str:
    if not any(marker in message for marker in REGION_QUESTION_MARKERS):
        return ""
    cities = (
        "campinas",
        "sao paulo",
        "são paulo",
        "rio de janeiro",
        "jundiai",
        "jundiaí",
        "valinhos",
        "vinhedo",
    )
    for city in cities:
        if city in message and city not in kb:
            if any(other in kb for other in cities if other != city):
                return f"kb_region_differs_from_question:{city}"
    return ""


def _numeric_qualifier_mismatch(message: str, kb: str) -> str:
    """Mesmo número, eixo factual diferente (orçamento vs instalação)."""
    if not re.search(r"\b48\s*h|\b48 horas\b", message):
        return ""
    if not re.search(r"\b48\s*h|\b48 horas\b", kb):
        return ""
    if _asks_execution_timeline(message) and _mentions_quote_timeline(kb) and not _mentions_execution_timeline(kb):
        return "same_number_different_qualifier:48h_quote_vs_execution"
    return ""


def effective_synthesis_mode(*, base_mode: str, assessment: EvidenceAssessment) -> str:
    if assessment.status == EvidenceSufficiency.PARTIAL:
        return "partial_inform"
    if assessment.status == EvidenceSufficiency.INSUFFICIENT:
        return "insufficient_safe"
    return base_mode


def _technical_requirement_gap(message: str, kb: str) -> str:
    missing = [
        term
        for term in TECHNICAL_REQUIREMENT_TERMS
        if term in message and (term not in kb or _term_marked_not_documented(kb, term))
    ]
    missing = [term for term in missing if not _term_supported_by_synonym(term, kb)]
    if not missing:
        return ""
    if "nasa" in missing:
        return "technical_requirement_not_in_knowledge_base:nasa"
    return "technical_requirement_not_in_knowledge_base:" + ",".join(missing[:3])


def _term_supported_by_synonym(term: str, kb: str) -> bool:
    if term in {"bateria"} and "autonomia" in kb:
        return True
    if term in {"peso", "pesa"} and "peso" in kb:
        return True
    if term in {"tensao", "tensão", "voltagem", "carrega", "carregar", "tomada"} and any(token in kb for token in ("tensao", "tensão", "voltagem", "alimentacao", "alimentação", "220 v", "110 v", "380 v")):
        return True
    return False


def _has_product_or_application_overlap(message: str, kb: str) -> bool:
    if any(term in message and term in kb for term in PRODUCT_OR_APPLICATION_TERMS):
        return True
    has_pronoun_reference = any(term in message for term in ("ele", "esse", "essa", "este", "esta", "modelo", "robo", "robô"))
    return has_pronoun_reference and any(term in kb for term in PRODUCT_OR_APPLICATION_TERMS)


def _term_marked_not_documented(kb: str, term: str) -> bool:
    for sentence in re.split(r"(?<=[.!?;])\s+|" + chr(10) + r"+", kb):
        if term not in sentence:
            continue
        if any(marker in sentence for marker in ("nao documentado", "não documentado", "nao afirmar", "não afirmar", "sem documentacao", "sem documentação")):
            return True
    return False
