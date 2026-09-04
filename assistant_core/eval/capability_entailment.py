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


PEOPLE_CONDITIONS = (
    "pessoas circul",
    "circulacao de pessoas",
    "fluxo de pessoas",
    "ambiente ocupado",
    "ambientes ocup",
    "pessoas no local",
    "pessoas no ambiente",
    "com pessoas",
    "gente passando",
    "ambientes com circul",
)

PEOPLE_CAPABILITY_MODAL_PATTERN = re.compile(
    r"(?:^|\b)(?:pode|consegue|suporta|permite|funciona|opera|trabalha|da conta)\b|"
    r"\be (?:capaz|adequado)\b",
)

PEOPLE_ACTIONS = (
    "operar",
    "trabalhar",
    "funcionar",
    "realizar",
    "executar",
    "apoiar",
    "fazer ",
    "limpeza",
)

EVALUATION_ONLY_LEADS = (
    "precisamos avaliar",
    "precisamos considerar",
    "a avaliacao ajuda",
    "avaliacao ajuda",
    "deve ser avaliad",
    "precisa ser avaliad",
    "e necessario verificar",
    "e necessario avaliar",
    "cita o fluxo",
    "fatores da avaliacao",
    "avaliar para melhorar",
    "buscar eficiencia",
    "vale avaliar",
    "sera um fator",
    "sera fator",
    "entendi",
    "considerar piso",
    "considerar tipo de piso",
    "rotina de limpeza",
)

CONSULTATIVE_SOFT_MODAL_PATTERN = re.compile(
    r"\bpode ser (?:avaliad|planejad|considerad|ajustad|adaptad|organizad)\b|"
    r"\bpode variar\b",
)

CAPABILITY_QUESTION_MARKERS = (
    "consegue trabalhar",
    "consegue operar",
    "consegue superar",
    "pode trabalhar",
    "pode operar",
    "pode apoiar",
    "pessoas circulando",
    "circulacao de pessoas",
    "circulação de pessoas",
    "com pessoas circul",
    "fluxo de pessoas",
    "e seguro oper",
    "e seguro trabalh",
    "e adequado para",
    "funciona com",
    "da conta de",
    "e capaz de",
    "autonomia",
    "quantas horas",
    "supera obstacul",
    "obstacul",
    "obstácul",
)

METADATA_LINE_PATTERN = re.compile(
    r"^(?:#+\s|tags\s*:|nome oficial\s*:|categoria\s*:|fonte\s*:|score\s*:|"
    r"chunk\s*:|document_id\s*:|source\s*:|referencia\s*:|referência\s*:|\[)",
    re.IGNORECASE,
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
    "deve ser avaliad",
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
        "kb_direct_patterns": (
            r"projetado para.{0,60}(?:operar|apoiar|trabalhar).{0,40}(?:pessoas|circul)",
            r"(?:pode|consegue|suporta|permite).{0,50}(?:operar|trabalhar|apoiar|realizar|executar).{0,40}(?:pessoas|circul|limpeza)",
            r"operar em ambientes.{0,40}(?:pessoas|circul)",
            r"(?:circulacao|circulação) de pessoas.{0,40}(?:oper|segur|permit|apoi)",
            r"ambientes com circulacao.{0,40}(?:pessoas|oper|apoi)",
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
    {
        "topic": "safety_efficiency",
        "question_markers": ("segur", "eficien", "risco"),
        "reply_claim_patterns": (
            r"garant(e|ir).{0,40}(?:segur|eficien)",
            r"(?:segur|eficien).{0,30}(?:garantid|assegur|confiavel)",
            r"operacao segura",
            r"operacao confiavel",
            r"sem riscos",
            r"\be seguro\b.{0,30}oper",
        ),
        "kb_direct_patterns": (
            r"operacao segura",
            r"sistemas documentados.{0,40}(?:segur|oper)",
            r"areas ocupadas",
            r"projetado para.{0,40}(?:segur|operacao segura)",
            r"segur.{0,30}(?:oper|areas ocupadas|documentad)",
        ),
        "kb_topic_markers": ("segur", "eficien", "risco"),
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

    if not _should_assess_entailment(message_norm, reply_norm):
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
    message_norm = _normalize(current_message)
    product = str(active_entity or "").strip()

    snippet = _relevant_kb_snippet(kb, message_norm)
    if snippet:
        lead = _limitation_lead(product, message_norm)
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


def sanitize_knowledge_snippet(text: str) -> str:
    """Remove headings, tags e metadata interna do RAG antes de citar ao visitante."""
    clean_lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = re.sub(r"^#+\s*", "", str(raw_line or "").strip())
        if not line:
            continue
        if METADATA_LINE_PATTERN.match(line):
            continue
        lower = line.lower()
        if lower in {"curated", "smart-control"} or lower.startswith("tags:"):
            continue
        if "curated" in lower and len(line) < 80 and ":" not in line:
            continue
        clean_lines.append(line)
    return "\n".join(clean_lines).strip()


def _relevant_kb_snippet(kb: str, message_norm: str) -> str:
    sanitized = sanitize_knowledge_snippet(kb)
    if not sanitized:
        return ""

    sentences: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", sanitized):
        cleaned = str(sentence or "").strip()
        if not cleaned or METADATA_LINE_PATTERN.match(cleaned):
            continue
        if cleaned.startswith("#"):
            continue
        sent_norm = _normalize(cleaned)
        if len(sent_norm) < 20:
            continue
        if any(marker in sent_norm for marker in ("tags:", "nome oficial:", "categoria:", "curated")):
            continue
        sentences.append(cleaned)

    for cleaned in sentences:
        sent_norm = _normalize(cleaned)
        for spec in TOPIC_SPECS:
            if not _question_targets_topic(message_norm, spec):
                continue
            if any(marker in sent_norm for marker in spec["kb_topic_markers"]):
                return cleaned[:280]
        if any(marker in sent_norm for marker in CONDITIONAL_KB_MARKERS):
            return cleaned[:280]

    return ""


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.lower()).strip()


def _is_primarily_limitation_reply(reply_norm: str) -> bool:
    hits = sum(1 for marker in LIMITATION_MARKERS if marker in reply_norm)
    return hits >= 2 or (hits == 1 and not _reply_has_strong_positive_claim(reply_norm))


def _reply_has_strong_positive_claim(reply_norm: str) -> bool:
    if reply_norm.startswith("sim,") or reply_norm.startswith("sim "):
        return True
    if _reply_claims_people_circulation(reply_norm):
        return True
    if any(re.search(pattern, reply_norm) for spec in TOPIC_SPECS for pattern in spec.get("reply_claim_patterns", ())):
        return True
    return False


def _question_targets_topic(message_norm: str, spec: dict) -> bool:
    return any(marker in message_norm for marker in spec["question_markers"])


def _reply_claims_topic(reply_norm: str, spec: dict) -> bool:
    topic = spec["topic"]
    if topic == "people_circulation":
        return _reply_claims_people_circulation(reply_norm)
    return any(re.search(pattern, reply_norm) for pattern in spec.get("reply_claim_patterns", ()))


def _should_assess_entailment(message_norm: str, reply_norm: str) -> bool:
    """Só avalia entailment em pergunta técnica ou resposta com afirmação forte."""
    if _is_capability_question(message_norm):
        return _reply_has_actionable_capability_signal(reply_norm)
    return False


def _is_capability_question(message_norm: str) -> bool:
    if any(marker in message_norm for marker in CAPABILITY_QUESTION_MARKERS):
        return True
    if "?" not in message_norm:
        return False
    return bool(
        re.search(
            r"\b(?:consegue|pode|suporta|funciona|e seguro|e adequado)\b",
            message_norm,
        )
    )


def _reply_has_actionable_capability_signal(reply_norm: str) -> bool:
    if _is_consultative_soft_reply(reply_norm):
        return False
    if _reply_has_strong_positive_claim(reply_norm):
        return True
    return False


def _is_consultative_soft_reply(reply_norm: str) -> bool:
    if CONSULTATIVE_SOFT_MODAL_PATTERN.search(reply_norm):
        return True
    return _is_evaluation_only_reply(reply_norm)


def _limitation_lead(product: str, message_norm: str) -> str:
    if product and _is_capability_question(message_norm):
        if any(marker in message_norm for marker in ("pessoas circul", "circulacao de pessoas", "consegue trabalhar", "pode trabalhar")):
            return (
                f"A documentação disponível não confirma diretamente se {product} "
                "pode trabalhar com pessoas circulando."
            )
        return f"A documentação disponível não confirma diretamente isso sobre {product}."
    if product:
        return f"A documentação disponível não confirma diretamente isso sobre {product}."
    return "A documentação disponível não confirma diretamente isso."


def _reply_claims_people_circulation(reply_norm: str) -> bool:
    if _is_consultative_soft_reply(reply_norm):
        return False
    has_condition = any(marker in reply_norm for marker in PEOPLE_CONDITIONS)
    if not has_condition:
        return False
    if _is_evaluation_only_reply(reply_norm):
        return False
    has_modal = bool(PEOPLE_CAPABILITY_MODAL_PATTERN.search(reply_norm))
    has_action = any(marker in reply_norm for marker in PEOPLE_ACTIONS)
    if has_modal and has_condition:
        return True
    if has_modal and has_action:
        return True
    return has_action and has_condition and has_modal


def _is_evaluation_only_reply(reply_norm: str) -> bool:
    if not any(lead in reply_norm for lead in EVALUATION_ONLY_LEADS):
        return False
    return not bool(PEOPLE_CAPABILITY_MODAL_PATTERN.search(reply_norm))


def _kb_directly_supports_topic(kb_norm: str, spec: dict) -> bool:
    return any(re.search(pattern, kb_norm) for pattern in spec["kb_direct_patterns"])


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
