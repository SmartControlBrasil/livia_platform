from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from knowledge_base.models import TenantRagDriveFileManifest

ENTITY_TYPE_PRODUCT = "product"

_GENERIC_TITLE_WORDS = {
    "manual", "catalogo", "catalog", "documento", "document", "ficha", "tecnica", "técnica",
    "especificacao", "especificação", "overview", "visao", "visão", "geral", "produto", "produtos",
}

_STOP_UPPER_PHRASES = {
    "fonte", "conteudo", "conteúdo", "tags", "observacoes", "observações", "atencao", "atenção",
}

_PRONOUN_OR_ELLIPTIC_RE = re.compile(
    r"\b(ele|ela|esse|essa|este|esta|desse|dessa|dele|dela|modelo|equipamento|robo|robô|produto)\b|^\s*e\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class KnowledgeEntity:
    canonical_name: str
    aliases: tuple[str, ...] = ()
    entity_type: str = ENTITY_TYPE_PRODUCT
    document_ids: tuple[int, ...] = ()
    confidence: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_subject(self) -> dict:
        return {
            "canonical_name": self.canonical_name,
            "entity_type": self.entity_type,
            "source_document_ids": list(self.document_ids),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class EntityResolution:
    subject: dict | None = None
    matches: tuple[KnowledgeEntity, ...] = ()
    ambiguous: bool = False
    ambiguity_options: tuple[str, ...] = ()
    confidence: float = 0.0
    method: str = "none"


def normalize_entity_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def extract_document_metadata(*, file_name: str, mime_type: str = "", relative_path: str = "", text: str = "", source_modified_time=None) -> dict:
    title = _document_title(file_name=file_name, relative_path=relative_path, text=text)
    doc_type = _document_type(file_name=file_name, mime_type=mime_type, text=text)
    candidates = _entity_candidates(file_name=file_name, title=title, text=text)
    product_names = _dedupe_preserve([c for c in candidates if _looks_like_product_name(c)])
    model_names = _dedupe_preserve([c for c in candidates if _looks_like_model_name(c)])
    if title and _looks_like_product_name(title):
        product_names = _dedupe_preserve([title, *product_names])
    return {
        "source_document_id": "",
        "file_name": str(file_name or ""),
        "document_title": title or None,
        "document_type": doc_type or None,
        "product_names": product_names,
        "model_names": model_names,
        "section": None,
        "heading": None,
        "page_number": None,
        "source_modified_time": source_modified_time.isoformat() if hasattr(source_modified_time, "isoformat") else None,
    }


def build_chunk_metadata(*, document_metadata: dict | None, text: str, start_char: int = 0) -> dict:
    base = dict(document_metadata or {})
    heading = _nearest_markdown_heading(str(text or ""))
    base.update({
        "section": heading,
        "heading": heading,
        "page_number": _page_number_hint(str(text or "")),
    })
    return base


def entity_catalog_for_tenant(tenant) -> list[KnowledgeEntity]:
    if tenant is None:
        return []
    docs = TenantRagDriveFileManifest.objects.filter(
        tenant=tenant,
        is_active=True,
    ).exclude(
        status__in=["failed", "removed", "unavailable", "skipped_unsupported"]
    ).order_by("id")
    grouped: dict[str, dict] = {}
    for manifest in docs:
        meta = dict(getattr(manifest, "document_metadata", None) or {})
        names = [*list(meta.get("product_names") or []), *list(meta.get("model_names") or [])]
        title = meta.get("document_title") or manifest.name
        if title and _looks_like_product_name(str(title)):
            names.append(str(title))
        for name in _dedupe_preserve(names):
            canonical = _canonicalize_name(name)
            if not canonical:
                continue
            key = normalize_entity_text(canonical)
            entry = grouped.setdefault(key, {"canonical": canonical, "aliases": set(), "documents": set(), "confidence": 0.75})
            entry["documents"].add(manifest.id)
            entry["aliases"].add(str(name).strip())
            for alias in meta.get("aliases") or []:
                if str(alias).strip():
                    entry["aliases"].add(str(alias).strip())
            for model in meta.get("model_names") or []:
                if str(model).strip():
                    entry["aliases"].add(str(model).strip())
    result = []
    for item in grouped.values():
        aliases = tuple(sorted({a for a in item["aliases"] if normalize_entity_text(a) != normalize_entity_text(item["canonical"])}))
        result.append(KnowledgeEntity(
            canonical_name=item["canonical"],
            aliases=aliases,
            document_ids=tuple(sorted(item["documents"])),
            confidence=float(item["confidence"]),
        ))
    return sorted(result, key=lambda e: normalize_entity_text(e.canonical_name))


def resolve_knowledge_entity(*, tenant, message: str, active_subject: dict | None = None) -> EntityResolution:
    msg_n = normalize_entity_text(message)
    if not msg_n:
        return EntityResolution()
    catalog = entity_catalog_for_tenant(tenant)
    scored: list[tuple[KnowledgeEntity, float, str]] = []
    for entity in catalog:
        names = (entity.canonical_name, *entity.aliases)
        best_score = 0.0
        best_method = "none"
        for name in names:
            name_n = normalize_entity_text(name)
            if not name_n:
                continue
            if _contains_phrase(msg_n, name_n):
                score = 1.0 if len(name_n.split()) > 1 else 0.86
                method = "exact"
            elif all(token in msg_n.split() for token in name_n.split()) and len(name_n.split()) > 1:
                score = 0.88
                method = "lexical"
            elif name_n in msg_n or msg_n in name_n:
                score = 0.62
                method = "partial"
            elif any(len(token) >= 4 and _contains_phrase(msg_n, token) for token in name_n.split()):
                score = 0.62
                method = "partial"
            else:
                continue
            if score > best_score:
                best_score = score
                best_method = method
        if best_score:
            scored.append((entity, best_score, best_method))
    scored.sort(key=lambda item: (-item[1], -len(item[0].canonical_name), item[0].canonical_name))
    if scored:
        top_score = scored[0][1]
        top = [item for item in scored if abs(item[1] - top_score) < 0.04]
        if len(top) > 1 and top_score < 0.9:
            options = tuple(entity.canonical_name for entity, _score, _method in top[:5])
            return EntityResolution(matches=tuple(entity for entity, _score, _method in top), ambiguous=True, ambiguity_options=options, confidence=top_score, method="ambiguous")
        entity, score, method = scored[0]
        return EntityResolution(subject=entity.to_subject(), matches=(entity,), confidence=score, method=method)
    if active_subject and _PRONOUN_OR_ELLIPTIC_RE.search(str(message or "")):
        confidence = float(active_subject.get("confidence") or 0.0)
        if confidence >= 0.55:
            return EntityResolution(subject=dict(active_subject), confidence=confidence, method="active_subject")
    return EntityResolution()


def _document_title(*, file_name: str, relative_path: str, text: str) -> str:
    for line in str(text or "").splitlines()[:20]:
        stripped = line.strip().strip("# ").strip()
        if stripped and len(stripped) <= 120 and not stripped.lower().startswith(("fonte:", "tags:", "conteúdo:", "conteudo:")):
            return _clean_title(stripped)
    stem = Path(str(file_name or relative_path or "")).stem.replace("_", " ").replace("-", " ").strip()
    return _clean_title(stem)


def _document_type(*, file_name: str, mime_type: str, text: str) -> str:
    blob = normalize_entity_text(f"{file_name} {mime_type} {str(text or '')[:500]}")
    if "manual" in blob:
        return "manual"
    if "catalogo" in blob or "catalog" in blob:
        return "catalog"
    if "ficha tecnica" in blob or "especificacao" in blob:
        return "technical_sheet"
    if "markdown" in blob or str(file_name).lower().endswith(".md"):
        return "markdown"
    if "pdf" in blob:
        return "pdf"
    return "document"


def _entity_candidates(*, file_name: str, title: str, text: str) -> list[str]:
    sample_lines = [title, Path(str(file_name or "")).stem.replace("_", " ").replace("-", " ")]
    sample_lines.extend(line.strip().strip("# ") for line in str(text or "").splitlines()[:40])
    candidates: list[str] = []
    for line in sample_lines:
        clean = _clean_title(line)
        if clean and _looks_like_product_name(clean):
            candidates.append(clean)
        candidates.extend(match.group(0).strip() for match in re.finditer(r"\b[A-Z][A-Z0-9]{1,}(?:[ -][A-Z0-9]{1,}){0,3}\b", line))
        candidates.extend(match.group(0).strip() for match in re.finditer(r"\b[A-Za-z]{2,}[ -]?[A-Z]{0,3}\d{1,4}[A-Z0-9-]*\b", line))
    return _dedupe_preserve(_clean_title(c) for c in candidates if c)


def _looks_like_product_name(value: str) -> bool:
    n = normalize_entity_text(value)
    raw = str(value or "").strip()
    if not n or n in _GENERIC_TITLE_WORDS or n in _STOP_UPPER_PHRASES:
        return False
    if ":" in raw:
        return False
    if any(word in n.split() for word in ("autonomia", "nominal", "alimentacao", "alimentação", "estacao", "estação", "peso", "operacional", "horas")):
        return False
    tokens = n.split()
    if len(tokens) > 5:
        return False
    has_digit = any(any(ch.isdigit() for ch in token) for token in tokens)
    all_capsish = bool(re.fullmatch(r"[A-Z0-9][A-Z0-9 -]{1,40}", raw))
    title_case_name = len(tokens) >= 2 and all(part[:1].isupper() for part in raw.split() if part[:1].isalpha())
    return has_digit or (all_capsish and len(tokens) > 1) or title_case_name


def _looks_like_model_name(value: str) -> bool:
    n = normalize_entity_text(value)
    return bool(n and any(ch.isdigit() for ch in n) and len(n) <= 40)


def _canonicalize_name(value: str) -> str:
    cleaned = _clean_title(value)
    if not cleaned:
        return ""
    words = [w for w in cleaned.split() if normalize_entity_text(w) not in _GENERIC_TITLE_WORDS]
    if not words:
        return ""
    return " ".join(words[:4]).strip()


def _clean_title(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip(" -_|"))
    text = re.sub(r"\.(pdf|docx|md|txt)$", "", text, flags=re.IGNORECASE)
    return text[:120].strip()


def _dedupe_preserve(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        value = str(value or "").strip()
        key = normalize_entity_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _contains_phrase(text_n: str, phrase_n: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase_n)}(?!\w)", text_n) is not None


def _nearest_markdown_heading(text: str) -> str | None:
    heading = None
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,6}\s+", stripped):
            heading = stripped.lstrip("#").strip()[:120]
    return heading


def _page_number_hint(text: str) -> int | None:
    match = re.search(r"(?:^|\n)\s*(?:pagina|página|page)\s+(\d{1,4})\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None
