"""Classificação mínima de conteúdo RAG: público vs policy/internal + domínio."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def _norm(value: str) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text)


POLICY_PATH_MARKERS = (
    "09_limites",
    "limites_e_nao_prometer",
    "nao_prometer",
    "não_prometer",
    "guardrail",
    "policy",
    "internal_policy",
    "system_prompt",
    "instrucoes_para_livia",
    "instruções_para_lívia",
)

POLICY_TEXT_MARKERS = (
    "o que nao prometer",
    "o que não prometer",
    "nao deve prometer",
    "não deve prometer",
    "a livia da smart control brasil nao deve",
    "a lívia da smart control brasil não deve",
    "como a livia deve",
    "como a lívia deve",
    "sem pedido explicito de orcamento, a livia",
    "sem pedido explícito de orçamento, a lívia",
    "limites tecnicos e comerciais",
    "limites técnicos e comerciais",
    "catalogo oficial apenas como backing",
    "catálogo oficial apenas como backing",
)

DOMAIN_PATH_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("robotics", ("01_xyron", "xyron", "hygibot", "dune", "duno", "liro", "neobot", "orbit", "buddy", "robo", "robô")),
    ("automation", ("02_mitsubishi", "mitsubishi", "automacao", "automação", "clp", "ihm")),
    ("maintenance", ("03_manutencao", "manutencao", "manutenção")),
    ("software_web", ("07_sistemas", "sistemas_python", "python", "loja virtual", "ecommerce")),
    ("materials", ("bancada", "granito", "marmore", "cozinha", "pitondo")),
    ("policy", POLICY_PATH_MARKERS),
)

INCOMPATIBLE_DOMAINS = {
    "robotics": {"software_web", "automation", "materials"},
    "software_web": {"robotics", "materials", "automation"},
    "materials": {"robotics", "software_web", "automation"},
    "automation": {"materials", "software_web", "robotics"},
}


@dataclass(frozen=True)
class ContentClassification:
    content_type: str  # public_knowledge | internal_policy | guardrail
    visibility: str  # public | internal
    domain: str
    product: str
    is_answerable: bool


def classify_rag_source(*, source_name: str = "", source_reference: str = "", text: str = "") -> ContentClassification:
    blob = _norm(" ".join([source_name, source_reference, (text or "")[:400]]))
    domain = "general"
    for candidate, markers in DOMAIN_PATH_RULES:
        if any(marker in blob for marker in markers):
            domain = candidate
            break

    is_policy = domain == "policy" or any(marker in blob for marker in POLICY_PATH_MARKERS)
    if not is_policy:
        is_policy = any(marker in blob for marker in POLICY_TEXT_MARKERS)

    product = ""
    if "duno" in blob or "dune" in blob or "hygibot" in blob:
        product = "Duno"
    elif "liro" in blob or "littlebot" in blob:
        product = "LIRO"
    elif "neobot" in blob:
        product = "NeoBot"
    elif "mitsubishi" in blob:
        product = "Mitsubishi"

    if is_policy:
        return ContentClassification(
            content_type="internal_policy",
            visibility="internal",
            domain="policy",
            product=product,
            is_answerable=False,
        )
    return ContentClassification(
        content_type="public_knowledge",
        visibility="public",
        domain=domain if domain != "policy" else "general",
        product=product,
        is_answerable=True,
    )


def is_policy_leak_text(text: str) -> bool:
    lowered = _norm(text)
    if not lowered:
        return False
    leak_markers = (
        "nao deve prometer",
        "não deve prometer",
        "o que nao prometer",
        "o que não prometer",
        "catalogo oficial apenas como backing",
        "catálogo oficial apenas como backing",
        "como a livia deve",
        "como a lívia deve",
        "sem pedido explicito de orcamento, a livia",
        "a livia da smart control brasil nao deve",
        "a lívia da smart control brasil não deve",
    )
    return any(marker in lowered for marker in leak_markers)


def domains_compatible(active_domain: str, chunk_domain: str) -> bool:
    active = str(active_domain or "").strip()
    chunk = str(chunk_domain or "").strip()
    if not active or not chunk or chunk in {"general", "policy"}:
        return chunk != "policy"
    if active == chunk:
        return True
    incompatible = INCOMPATIBLE_DOMAINS.get(active, set())
    return chunk not in incompatible
