from __future__ import annotations

from .retriever import retrieve_relevant_knowledge


def build_knowledge_context(tenant, message, service_area=None, limit=3):
    snippets = retrieve_relevant_knowledge(tenant, message, service_area=service_area, limit=limit)
    if not snippets:
        return ""

    lines = ["Base de conhecimento encontrada:"]
    for index, snippet in enumerate(snippets, start=1):
        excerpt = snippet.excerpt.strip()
        if len(excerpt) > 260:
            excerpt = excerpt[:257].rstrip() + "..."
        lines.append(f"{index}. {snippet.title}: {excerpt}")
    return "\n".join(lines)
