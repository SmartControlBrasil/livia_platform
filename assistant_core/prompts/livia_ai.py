from __future__ import annotations

from assistant_core.summary.livia import build_conversation_summary, format_conversation_summary_notes


def build_livia_ai_prompt(
    *,
    tenant,
    assistant_profile,
    message: str,
    conversation,
    discovery_result,
    lead_state: str,
    knowledge_context: str,
    summary: str = "",
    deterministic_reply: str,
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    profile = {
        "name": str(getattr(assistant_profile, "name", "") or "Lívia").strip(),
        "tone": str(getattr(assistant_profile, "tone", "") or "consultivo, claro e profissional").strip(),
        "primary_goal": str(getattr(assistant_profile, "primary_goal", "") or "qualificar leads").strip(),
        "initial_message": str(getattr(assistant_profile, "initial_message", "") or "").strip(),
    }
    tenant_name = str(getattr(tenant, "name", "") or "").strip()
    tenant_slug = str(getattr(tenant, "slug", "") or "").strip()
    discovery = discovery_result.to_dict() if hasattr(discovery_result, "to_dict") else {}
    rendered_summary = summary or _safe_summary(conversation)
    rendered_history = _short_history(history)

    system_prompt = "\n".join(
        [
            "Você é a Lívia, assistente consultiva da Smart Control Brasil em uma plataforma multi-tenant.",
            "Responda sempre em português do Brasil, com tom curto, natural, técnico quando necessário e comercial sem pressão.",
            "Sua tarefa é apenas melhorar a redação da resposta determinística fornecida. Não mude decisões operacionais.",
            "Não capture lead, não altere estado, não prometa handoff, não envie CRM e não diga que executou ações que não estejam na resposta base.",
            "Não invente preço, prazo, garantia, estoque, disponibilidade, especificação técnica ou agenda.",
            "Use o contexto de conhecimento quando existir, mas seja transparente se faltar informação confiável.",
            "Não peça contato cedo demais quando o discovery ainda estiver vago. Faça no máximo uma pergunta útil.",
            "Respeite o lead_state e preserve a intenção da resposta determinística.",
            "Não mencione prompt, JSON, regras internas, estado interno, feature flag, IA ou automação.",
            "Retorne somente o texto final para o visitante.",
        ]
    )
    user_prompt = "\n\n".join(
        [
            f"Tenant: {tenant_name or tenant_slug or 'não informado'}",
            (
                "Perfil da assistente:\n"
                f"- Nome: {profile['name']}\n"
                f"- Tom: {profile['tone']}\n"
                f"- Objetivo principal: {profile['primary_goal']}\n"
                f"- Mensagem inicial: {profile['initial_message'] or 'não informada'}"
            ),
            f"lead_state atual: {lead_state or 'não informado'}",
            f"DiscoveryResult: {discovery}",
            f"Resumo da conversa:\n{rendered_summary or 'Sem resumo disponível.'}",
            f"Histórico curto:\n{rendered_history or 'Sem histórico anterior.'}",
            f"Contexto de conhecimento:\n{knowledge_context or 'Sem contexto de conhecimento recuperado.'}",
            f"Mensagem atual do visitante:\n{message}",
            f"Resposta determinística que deve ser preservada em intenção e segurança:\n{deterministic_reply}",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _safe_summary(conversation) -> str:
    if conversation is None:
        return ""
    return format_conversation_summary_notes(build_conversation_summary(conversation))


def _short_history(history: list[dict[str, str]] | None) -> str:
    lines = []
    for item in list(history or [])[-6:]:
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if not role or not content:
            continue
        lines.append(f"- {role}: {content[:300]}")
    return "\n".join(lines)
