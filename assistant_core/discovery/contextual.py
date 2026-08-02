from __future__ import annotations

from assistant_core.discovery.livia import AREA_QUESTIONS


def resolve_discovery_question(
    service_area: str,
    *,
    business_domain: str = "",
    business_name: str = "",
) -> str:
    area = str(service_area or "unknown").strip() or "unknown"
    if area in AREA_QUESTIONS and area != "unknown":
        return AREA_QUESTIONS[area]

    domain = str(business_domain or "").strip()
    if domain:
        return (
            f"Claro. Para eu te orientar melhor sobre {domain}, "
            "pode me contar um pouco mais do seu projeto ou da aplicação?"
        )

    name = str(business_name or "").strip()
    if name:
        return f"Claro. Para eu te orientar melhor, pode me contar um pouco mais do que você precisa?"

    return (
        "Claro. Para eu te orientar melhor, pode me contar um pouco mais "
        "do contexto ou da aplicação que você tem em mente?"
    )
