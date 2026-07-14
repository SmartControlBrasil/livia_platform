from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from knowledge_base.models import KnowledgeDocument


STOPWORDS = {
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
    "e", "em", "o", "os", "para", "por", "que", "um", "uma", "voces", "voce",
    "tem", "sobre", "preciso", "quero", "saber",
}
ALIASES = {
    "robo": {"robotica", "xyron", "hygibot"},
    "robos": {"robo", "robotica", "xyron"},
    "limpeza": {"hygibot", "higienizacao", "robo"},
    "higienizacao": {"limpeza", "hygibot"},
    "clp": {"automacao", "mitsubishi"},
    "mitsubishi": {"automacao", "clp", "ihm", "inversor", "servo"},
    "esteira": {"academia", "manutencao"},
    "academia": {"esteira", "manutencao"},
    "ia": {"agente", "livia", "atlas", "software"},
}
SERVICE_AREA_TERMS = {
    "automation": {"automacao", "mitsubishi", "clp", "ihm", "inversor", "servo", "retrofit"},
    "robotics": {"robotica", "robo", "robos", "xyron", "hygibot", "limpeza"},
    "maintenance": {"manutencao", "esteira", "academia", "tecnica", "equipamento"},
    "software_web": {"software", "sistema", "web", "agente", "ia", "livia", "atlas"},
}


@dataclass(frozen=True)
class KnowledgeSnippet:
    title: str
    excerpt: str
    source_url: str
    score: int
    tags: list[str]


def retrieve_relevant_knowledge(tenant, query, service_area=None, limit=3):
    if tenant is None:
        return []
    terms = _terms(query)
    if not terms:
        return []

    documents = KnowledgeDocument.objects.filter(tenant=tenant, status=KnowledgeDocument.Status.ACTIVE)
    snippets = []
    for document in documents:
        score = _score_document(document, terms, service_area)
        if score <= 0:
            continue
        snippets.append(KnowledgeSnippet(
            title=document.title,
            excerpt=_excerpt(document.content, terms),
            source_url=document.source_url,
            score=score,
            tags=_clean_tags(document.tags),
        ))

    snippets.sort(key=lambda item: (item.score, item.title.lower()), reverse=True)
    return snippets[:limit]


def _score_document(document, terms, service_area):
    title = _normalize(document.title)
    content = _normalize(document.content)
    tags = { _normalize(tag) for tag in _clean_tags(document.tags) }
    source_type = _normalize(document.source_type)
    corpus = f"{title} {content} {' '.join(tags)} {source_type}"
    score = 0
    for term in terms:
        score += title.count(term) * 9
        score += min(content.count(term), 8) * 3
        if term in tags:
            score += 8
        if re.search(rf"(?:^|\W){re.escape(term)}(?:$|\W)", corpus):
            score += 2
    if service_area and service_area != "unknown":
        area_terms = SERVICE_AREA_TERMS.get(service_area, set())
        if terms.intersection(area_terms) or tags.intersection(area_terms) or any(term in corpus for term in area_terms):
            score += 14
        elif service_area not in tags:
            score -= 3
    return score


def _excerpt(content, terms, max_chars=260):
    clean = " ".join(str(content or "").split())
    if not clean:
        return ""
    normalized = _normalize(clean)
    first_match = min((normalized.find(term) for term in terms if normalized.find(term) >= 0), default=-1)
    if first_match < 0:
        return clean[:max_chars].rstrip() + ("..." if len(clean) > max_chars else "")
    start = max(0, first_match - 70)
    end = min(len(clean), start + max_chars)
    excerpt = clean[start:end].strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(clean):
        excerpt += "..."
    return excerpt


def _terms(text):
    normalized = _normalize(text)
    terms = {term for term in re.findall(r"[a-z0-9-]{2,}", normalized) if term not in STOPWORDS}
    for term in tuple(terms):
        terms.update(ALIASES.get(term, set()))
    return terms


def _normalize(text):
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(char for char in text if not unicodedata.combining(char))


def _clean_tags(tags):
    if isinstance(tags, list):
        return [str(tag).strip() for tag in tags if str(tag).strip()]
    if isinstance(tags, str):
        return [tag.strip() for tag in tags.split(",") if tag.strip()]
    return []
