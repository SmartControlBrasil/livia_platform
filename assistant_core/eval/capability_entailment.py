"""Verificação determinística de entailment para claims técnicos/capacidade."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from assistant_core.eval.evidence_sufficiency import extract_knowledge_text


@dataclass(frozen=True)
class CapabilityEntailmentResult:
    unsupported: bool = False
    reason: str = ""
    topic: str = ""


POSITIVE_CAPABILITY_VERBS = (
    r"\bpode\b",
    r"\bconsegue\b",
    r"\bsuporta\b",
    r"\boperar\b",
    r"\bopera\b",
    r"\bsupera\b",
    r"\bultrapassa\b",
    r"\bpossui\b",
    r"\btem autonomia\b",
    r"\be (?:adequado|seguro|compativel|compatível)\b",
    r"\bfoi projetado para\b",
    r"\bprojetado para operar\b",
)

LIMITATION_MARKERS = (
    "nao confirma",
    "não confirma",
    "nao ha confirmacao",
    "não há confirmação",
    "nao encontrei confirmacao",
    "não encontrei confirmação",
    "documentacao disponivel nao confirma",
    "documentação disponível não confirma",
    "precisa ser avaliado",
    "precisa ser avaliada",
    "nao ha informacao suficiente",
    "não há informação suficiente",
)

CONDITIONAL_KB_MARKERS = (
    "depende de",
    "depende do",
    "depende da",
    "a escolha depende",
    "conforme o",
    "conforme a",
    "deve avaliar",
    "precisa avaliar",
    "precisa ser avaliad",
    "necessario avaliar",
    "necessário avaliar",
)

REPLY_STRENGTHENING_MARKERS = (
    "garantid",
    "assegur",
    "certamente",
    " ininterrupt",
    "continuamente",
)

TOPIC_SPECS: tuple[dict, ...] = (
    {
        "topic": "people_circulation",
        "question_markers": (
            "pessoas circulando",
            "pessoas circul",
            "fluxo de pessoas",
            "com pessoas",
            "gente passando",
            "circulacao de pessoas",
            "circulação de pessoas",
        ),
        "reply_claim_patterns": (
            r"(?:pode|consegue|suporta|e capaz de|é capaz de).{0,50}(?:operar|trabalhar|funcionar).{0,50}(?:pessoas|circul)",
            r"(?:pode|consegue|suporta).{0,30}operar",
            r"operar.{0,40}(?:pessoas|circul|ambientes com)",
        ),
        "kb_direct_patterns": (
            r"projetado para operar.{0,60}(?:pessoas|circul)",
            r"(?:pode|consegue|suporta|permite).{0,40}(?:operar|trabalhar).{0,40}(?:pessoas|circul)",
            r"operar em ambientes.{0,40}(?:pessoas|circul)",
            r"(?:circulacao|circulação) de pessoas.{0,40}(?:oper|segur|permit)",
        ),
        "kb_topic_markers": ("fluxo de pessoas", "pessoas", "circul"),
    },
    {
        "topic": "obstacle_handling",
        "question_markers": ("obstacul", "obstácul"),
        "reply_claim_patterns": (
            r"supera.{0,30}obstacul",
            r"ultrapassa.{0,30}obstacul",
            r"(?:pode|consegue).{0,30}(?:superar|ultrapassar|evitar).{0,20}obstacul",
            r"automaticamente.{0,20}obstacul",
        ),
        "kb_direct_patterns": (
            r"(?:supera|ultrapassa|detecta|evita|navega).{0,30}obstacul",
            r"obstacul.{0,30}(?:automatic|detect|evit|supera|ultrapass)",
        ),
        "kb_topic_markers": ("obstacul", "obstácul"),
    },
    {
        "topic": "autonomy_duration",
        "question_markers": ("autonomia", "bateria", "quantas horas", "quanto tempo dura"),
        "reply_claim_patterns": (
            r"autonomia.{0,20}\d",
            r"\d.{0,10}horas",
            r"possui autonomia",
            r"tem autonomia",
        ),
        "kb_direct_patterns": (
            r"autonomia.{0,20}\d",
            r"\d.{0,10}horas",
        ),
        "kb_topic_markers": ("autonomia", "horas", "bateria"),
    },
)


def assess_capability_entailment(
    *,
    reply: str,
    knowledge_context: str,
    current_message: str,
) -> CapabilityEntailmentResult:
    """True quando a resposta afirma capacidade não sustentada diretamente pela KB."""
    kb = extract_knowledge_text(knowledge_context)
    if not kb:
        return CapabilityEntailmentResult()

    reply_norm = _normalize(reply)
    kb_norm = _normalize(kb)
    message_norm = _normalize(current_message)

    if not reply_norm or _is_primarily_limitation_reply(reply_norm):
        return CapabilityEntailmentResult()

    if not _reply_has_positive_capability_claim(reply_norm):
        return CapabilityEntailmentResult()

    for spec in TOPIC_SPECS:
        if not _reply_claims_topic(reply_norm, spec):
            continue
        if spec["topic"] == "autonomy_duration":
            if _autonomy_numeric_mismatch(reply_norm, kb_norm, spec):
                return CapabilityEntailmentResult(
                    unsupported=True,
                    reason="autonomy_not_in_knowledge_base",
                    topic=spec["topic"],
                )
            if _reply_overstates_kb_qualifier(reply_norm, kb_norm):
                return CapabilityEntailmentResult(
                    unsupported=True,
                    reason="qualifier_mismatch",
                    topic=spec["topic"],
                )
            if _kb_directly_supports_topic(kb_norm, spec):
                continue
            return CapabilityEntailmentResult(
                unsupported=True,
                reason="autonomy_not_in_knowledge_base",
                topic=spec["topic"],
            )
        if _kb_directly_supports_topic(kb_norm, spec):
            continue
        return CapabilityEntailmentResult(
            unsupported=True,
            reason="conditional_or_missing_support",
            topic=spec["topic"],
        )

    return CapabilityEntailmentResult()


def build_grounded_limitation_reply(
    *,
    knowledge_context: str,
    current_message: str,
    active_entity: str = "",
) -> str:
    """Resposta natural de limitação usando trechos condicionais da KB."""
    kb = extract_knowledge_text(knowledge_context)
    kb_norm = _normalize(kb)
    message_norm = _normalize(current_message)
    product = str(active_entity or "").strip()

    snippet = _relevant_kb_snippet(kb, message_norm)
    if snippet:
        lead = "A documentação disponível não confirma diretamente isso."
        if product:
            lead = f"A documentação disponível não confirma diretamente isso sobre {product}."
        body = snippet.rstrip(".")
        if any(marker in _normalize(snippet) for marker in CONDITIONAL_KB_MARKERS):
            tail = "então esse ponto precisa ser avaliado conforme o ambiente."
        else:
            tail = "esse ponto precisa ser confirmado com a equipe técnica."
        return f"{lead} Ela informa que {body}, {tail}"

    if product:
        return (
            f"Não encontrei confirmação suficiente na documentação disponível sobre {product} "
            "para responder isso com segurança."
        )
    return "Não encontrei confirmação suficiente na documentação disponível para responder isso com segurança."


def _relevant_kb_snippet(kb: str, message_norm: str) -> str:
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", str(kb or "")):
        cleaned = str(sentence or "").strip()
        if not cleaned:
            continue
        sent_norm = _normalize(cleaned)
        for spec in TOPIC_SPECS:
            if not _question_targets_topic(message_norm, spec):
                continue
            if any(marker in sent_norm for marker in spec["kb_topic_markers"]):
                return cleaned[:280]
        if any(marker in sent_norm for marker in CONDITIONAL_KB_MARKERS):
            return cleaned[:280]
    first = str(kb or "").strip().split(".")[0].strip()
    return first[:280] if first else ""


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.lower()).strip()


def _is_primarily_limitation_reply(reply_norm: str) -> bool:
    hits = sum(1 for marker in LIMITATION_MARKERS if marker in reply_norm)
    return hits >= 2 or (hits == 1 and not _reply_has_strong_positive_claim(reply_norm))


def _reply_has_positive_capability_claim(reply_norm: str) -> bool:
    return _reply_has_strong_positive_claim(reply_norm)


def _reply_has_strong_positive_claim(reply_norm: str) -> bool:
    if reply_norm.startswith("sim,") or reply_norm.startswith("sim "):
        return True
    return any(re.search(pattern, reply_norm) for pattern in POSITIVE_CAPABILITY_VERBS)


def _question_targets_topic(message_norm: str, spec: dict) -> bool:
    return any(marker in message_norm for marker in spec["question_markers"])


def _reply_claims_topic(reply_norm: str, spec: dict) -> bool:
    return any(re.search(pattern, reply_norm) for pattern in spec["reply_claim_patterns"])


def _kb_directly_supports_topic(kb_norm: str, spec: dict) -> bool:
    return any(re.search(pattern, kb_norm) for pattern in spec["kb_direct_patterns"])


def _kb_mentions_topic(kb_norm: str, spec: dict) -> bool:
    return any(marker in kb_norm for marker in spec["kb_topic_markers"])


def _autonomy_numeric_mismatch(reply_norm: str, kb_norm: str, spec: dict) -> bool:
    if spec["topic"] != "autonomy_duration":
        return False
    if not _reply_claims_topic(reply_norm, spec):
        return False
    reply_numbers = _extract_duration_numbers(reply_norm)
    if not reply_numbers:
        return False
    if "autonomia" not in kb_norm and "horas" not in kb_norm and "bateria" not in kb_norm:
        return True
    kb_numbers = _extract_duration_numbers(kb_norm)
    if not kb_numbers:
        return True
    if reply_numbers.isdisjoint(kb_numbers):
        return True
    return False


def _extract_duration_numbers(text_norm: str) -> set[str]:
    numbers: set[str] = set()
    for match in re.finditer(r"(\d+).{0,12}(?:horas?|h\b)", text_norm):
        numbers.add(match.group(1))
    if "autonomia" in text_norm:
        numbers.update(re.findall(r"\d+", text_norm))
    return numbers


def _reply_overstates_kb_qualifier(reply_norm: str, kb_norm: str) -> bool:
    reply_strengthens = any(marker in reply_norm for marker in REPLY_STRENGTHENING_MARKERS)
    kb_strengthens = any(marker in kb_norm for marker in REPLY_STRENGTHENING_MARKERS)
    if reply_strengthens and not kb_strengthens:
        return True
    return False
