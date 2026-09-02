from __future__ import annotations

import re
import unicodedata

from assistant_core.prompts.livia import DEFAULT_REPLY

BOM = "\ufeff"

TITLE_PREFIXES = (
    "orientação de indicação",
    "limites técnicos e comerciais",
    "automação mitsubishi electric",
    "lacunas de curadoria",
    "robô educacional interativo",
    "robô de limpeza",
    "bancadas de cozinha, pias",
    "perguntas frequentes sustentadas",
    "processo de orçamento, medidas",
    "fatos confirmados pelo site",
    "empresa e atendimento",
    "mármores, granitos e materiais",
    "marmores, granitos e materiais",
    "áreas gourmet",
    "areas gourmet",
    "projetos comerciais",
    "conservação de bancadas",
    "conservacao de bancadas",
)

META_MARKERS = (
    "marque qualquer",
    "ignore as regras",
    "altere o tenant",
    "system prompt",
    "você deve sempre",
    "crie lead",
    "score:",
    "fonte:",
    "documento:",
    "chunk:",
    "curadoria anhembi",
    "submetido em",
    "centro universitário",
    "arquivo 1 -",
    "status: sete unidades",
    "como a lívia deve",
    "como a livia deve",
    "não inventar modelo",
    "nao inventar modelo",
    "catálogos detalhados por modelo",
    "catalogos detalhados por modelo",
    "quando perguntado sobre esses pontos",
    "a resposta deve pedir confirmação",
    "templates/institutional",
    "sem pedido explícito de orçamento, a lívia",
    "sem pedido explicito de orcamento, a livia",
    "a lívia explica fatores",
    "`templates/",
    "não deve prometer",
    "nao deve prometer",
    "o que não prometer",
    "o que nao prometer",
    "a lívia da smart control brasil não deve",
    "a livia da smart control brasil nao deve",
    "catálogo oficial apenas como backing",
    "catalogo oficial apenas como backing",
    "limites técnicos e comerciais",
    "limites tecnicos e comerciais",
    "substituição completa da equipe",
    "substituicao completa da equipe",
)

CONTEXT_TOKEN_ENRICHMENTS = (
    "cooktop", "granito", "marmore", "mármore", "nicho", "escada", "gourmet",
    "churrasqueira", "pia", "ilha", "frontao", "frontão", "cuba", "lavabo",
    "banheiro", "cozinha", "escola", "clp", "ihm", "mitsubishi", "xyron",
    "python", "site", "loja", "orcamento", "orçamento", "sim", "nao", "não",
)


def normalize_text(value: str) -> str:
    normalized = str(value or "").strip().lower().replace(BOM, "")
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", normalized)


def is_generic_fallback_reply(reply: str) -> bool:
    cleaned = normalize_text(reply)
    if not cleaned:
        return False
    defaults = {
        normalize_text(DEFAULT_REPLY),
        "entendi. pode me explicar um pouco mais?",
        "entendi. pode me explicar um pouco mais para eu te orientar.",
        "perfeito. me conte um pouco mais sobre o contexto para eu te orientar.",
    }
    return cleaned in defaults or cleaned.startswith("entendi. pode me explicar um pouco mais")


def synthesize_deterministic_reply(
    knowledge_context: str,
    *,
    base_reply: str = "",
    max_sentences: int = 2,
    max_chars: int = 420,
) -> str:
    hints = _extract_safe_bits(knowledge_context)
    synthesized = _sentences_from_bits(hints, max_sentences=max_sentences, max_chars=max_chars)
    synthesized = _strip_site_meta_phrasing(synthesized)
    base = str(base_reply or "").strip()
    if not synthesized:
        return base
    if not base or is_generic_fallback_reply(base):
        return synthesized
    # Preço conceitual / policy comercial: resposta de preço prevalece (não inventar ficha + preço).
    base_n = normalize_text(base)
    if any(token in base_n for token in ("investimento varia", "valor fechado", "nao tenho um valor", "não tenho um valor")):
        return base
    if normalize_text(synthesized) in normalize_text(base):
        return base
    return f"{synthesized}\n\n{base}"


def _strip_site_meta_phrasing(text: str) -> str:
    cleaned = str(text or "")
    replacements = (
        (r"(?i)\bO site cita\b", "Há referências a"),
        (r"(?i)\bO site informa\b", "Em geral,"),
        (r"(?i)\bO site orienta que\b", ""),
        (r"(?i)\bO site descreve\b", ""),
        (r"(?i)\bFonte: página oficial[^.]*\.\s*", ""),
    )
    for pattern, repl in replacements:
        cleaned = re.sub(pattern, repl, cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def consultative_followup_for_context(need_or_message: str) -> str:
    normalized = normalize_text(need_or_message)
    # Domínios específicos ANTES de heurísticas genéricas de "produto/site".
    if any(token in normalized for token in ("cozinha", "bancada", "cooktop", "pia", "ilha", "granito", "marmore", "mármore")):
        return "Você já tem medidas aproximadas ou fotos da bancada?"
    if any(token in normalized for token in ("banheiro", "nicho", "lavabo", "cuba")):
        return "O projeto já inclui bancada, cuba e nicho, ou só parte disso?"
    if any(token in normalized for token in ("duno", "dune", "hygibot", "limpeza", "lavar", "varrer", "aspirar")):
        return "Qual é o ambiente e o tipo de piso onde a limpeza acontece?"
    if any(token in normalized for token in ("escola", "educac", "professor")) and "limpeza" not in normalized:
        return "O foco é robótica educacional, demonstração ou outro objetivo na escola?"
    if any(token in normalized for token in ("automacao", "automação", "clp", "mitsubishi", "robo", "robô", "robotica", "robótica", "xyron")):
        return "Qual ambiente e objetivo você quer cobrir primeiro?"
    # E-commerce só com sinais claros de loja/site — nunca só "produto".
    if any(token in normalized for token in ("loja virtual", "ecommerce", "e-commerce", "loja online")):
        return "Você pretende começar com poucos produtos ou já tem um catálogo maior?"
    if any(token in normalized for token in ("site institucional", "landing page", "pagina web", "página web")):
        return "O foco é divulgação, captura de contatos ou outro objetivo do site?"
    return "Qual detalhe é mais importante agora para eu te orientar melhor?"


def prefer_contextual_reply_over_fallback(
    *,
    knowledge_context: str = "",
    need_summary: str = "",
    current_message: str = "",
    history=None,
    active_domain: str = "",
    active_entity: str = "",
    skip_followup: bool = False,
) -> str:
    synthesized = synthesize_deterministic_reply(knowledge_context, base_reply="")
    context_blob = " ".join(
        [
            str(active_domain or ""),
            str(active_entity or ""),
            str(need_summary or ""),
            str(current_message or ""),
            " ".join(str(item.get("content") or "") for item in (history or []) if item.get("role") == "user"),
        ]
    ).strip()
    if synthesized:
        if skip_followup:
            return synthesized
        follow = consultative_followup_for_context(context_blob or current_message)
        # Nunca anexar follow-up de catálogo fora de software_web/ecommerce.
        if "catálogo maior" in follow.lower() or "catalogo maior" in normalize_text(follow):
            if active_domain and active_domain != "software_web":
                return synthesized
            if any(token in normalize_text(context_blob) for token in ("robo", "robô", "duno", "limpeza", "mitsubishi", "bancada")):
                return synthesized
        if follow and follow.lower() not in synthesized.lower():
            return f"{synthesized} {follow}".strip()
        return synthesized
    if skip_followup:
        return DEFAULT_REPLY if not context_blob else (
            f"Posso te orientar sobre {active_entity}." if active_entity else DEFAULT_REPLY
        )
    if context_blob:
        return consultative_followup_for_context(context_blob)
    return DEFAULT_REPLY


def is_short_context_token(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if normalized in CONTEXT_TOKEN_ENRICHMENTS:
        return True
    words = normalized.split()
    return len(words) <= 2 and any(token in normalized for token in CONTEXT_TOKEN_ENRICHMENTS)


def _extract_safe_bits(knowledge_context: str) -> list[str]:
    text = str(knowledge_context or "").replace(BOM, "")
    if not text.strip():
        return []

    contents: list[str] = []
    capture = False
    buffer: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().replace(BOM, "")
        if not line:
            if capture and buffer:
                contents.append(" ".join(buffer).strip())
                buffer = []
                capture = False
            continue
        upper = line.upper()
        if upper.startswith("[KNOWLEDGE_BASE]") or upper.startswith("[/KNOWLEDGE_BASE]"):
            if buffer:
                contents.append(" ".join(buffer).strip())
                buffer = []
            capture = False
            continue
        lowered = line.lower()
        if lowered.startswith(("o bloco abaixo", "trate o conteúdo", "pedido de mudança")):
            continue
        if lowered.startswith(("## fatos confirmados", "# fatos confirmados", "fatos confirmados pelo site")):
            continue
        if lowered.startswith(("fonte:", "referência:", "referencia:", "score:", "documento:", "chunk:")):
            if buffer:
                contents.append(" ".join(buffer).strip())
                buffer = []
            capture = False
            continue
        if lowered.startswith("conteúdo:") or lowered.startswith("conteudo:"):
            if buffer:
                contents.append(" ".join(buffer).strip())
                buffer = []
            capture = True
            remainder = line.split(":", 1)[1].strip()
            remainder_plain = remainder.lstrip("# ").strip()
            if remainder_plain and not remainder_plain.lower().startswith(
                ("fatos confirmados", "bancadas de cozinha, pias", "perguntas frequentes", "processo de orçamento")
            ):
                buffer.append(remainder_plain)
            continue
        if lowered.startswith("#"):
            continue
        if capture:
            buffer.append(line)
        elif len(line) >= 40 and not lowered.startswith(("tags:", "##")):
            contents.append(line)
    if buffer:
        contents.append(" ".join(buffer).strip())

    safe_bits: list[str] = []
    seen: set[str] = set()
    for item in contents:
        clipped = _clean_bit(item)
        if not clipped:
            continue
        key = normalize_text(clipped)[:120]
        if key in seen:
            continue
        if any(normalize_text(clipped).startswith(existing[:80]) or existing.startswith(key[:80]) for existing in seen):
            continue
        seen.add(key)
        safe_bits.append(clipped)
        if len(safe_bits) >= 3:
            break
    return safe_bits


def _clean_bit(item: str) -> str:
    clipped = " ".join(str(item or "").replace(BOM, "").split()).strip()
    clipped = re.sub(r"^#+\s*", "", clipped)
    clipped = re.sub(r"(?:^|\s)#+\s*", " ", clipped)
    clipped = clipped.replace("Tags: smart-control, curated", "").replace("Tags: smart-control", "")
    clipped = " ".join(clipped.split()).strip(" -•#")
    if clipped.lower().startswith("tags:"):
        return ""
    from knowledge_base.rag.content_classification import is_policy_leak_text

    if is_policy_leak_text(clipped):
        return ""
    # Remove seções explícitas de limites/não prometer do texto público.
    clipped = re.split(r"(?i)\b(?:o que n[aã]o prometer|limites\b)\b", clipped)[0].strip()
    for prefix in (
        "robotica xyron visao geral",
        "robótica de serviço xyron",
        "faq comercial smart control brasil",
        "automacao mitsubishi",
        "automação industrial mitsubishi",
        "sistemas python e web",
        "limites e nao prometer",
        "limites e não prometer",
        "hygibot / dune bot",
        "hygibot / duno bot",
    ):
        lowered_full = clipped.lower()
        if lowered_full.startswith(prefix):
            clipped = clipped[len(prefix) :].strip(" :-•#")
            break
    body_match = re.search(
        r"((?:A|O|Os|As|Um|Uma|Este|Esta|A\s+Grani|A\s+Smart|Granimármores|Granimarmores|Smart\s+Control|Robô|Robo|O\s+Duno|O\s+HygiBot)[^.]{25,}[.!?]?)",
        clipped,
    )
    if body_match:
        clipped = body_match.group(1).strip()
    else:
        capital = re.search(r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][^.]{39,}[.!]?", clipped)
        if capital:
            clipped = capital.group(0).strip()
    clipped = re.sub(r"`?templates/institutional/[^`\s]+`?", "", clipped)
    clipped = re.sub(r"\s+", " ", clipped).strip()
    # Descarta cortes de título tipo "O / Little Bot Categoria:"
    if re.match(r"^[A-Za-zÀ-ÿ]?\s*/\s*[A-Za-z]", clipped) or "categoria:" in clipped.lower()[:40]:
        body = re.search(r"\b((?:Robô|Robo|Indicar|Atendemos|Desenvolvemos|Integra|Apoia|Apoiar)\b.+)$", clipped)
        clipped = body.group(1).strip() if body else ""
    clipped = clipped[:280].strip(" -•#")
    if len(clipped) < 40:
        return ""
    if clipped and clipped[0].islower():
        capital = re.search(r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ].{39,}", clipped)
        clipped = capital.group(0).strip() if capital else ""
        if len(clipped) < 40:
            return ""
    lowered = clipped.lower()
    if any(marker in lowered for marker in META_MARKERS):
        return ""
    if is_policy_leak_text(clipped):
        return ""
    if lowered.startswith(TITLE_PREFIXES) and len(clipped) < 90:
        return ""
    return clipped


def _sentences_from_bits(bits: list[str], *, max_sentences: int, max_chars: int) -> str:
    sentences: list[str] = []
    seen: set[str] = set()
    for hints in bits:
        for part in re.split(r"(?<=[.!?])\s+", hints):
            clean = " ".join(part.split()).strip(" -•#")
            clean = re.sub(r"^#+\s*", "", clean).strip()
            if len(clean) < 35:
                continue
            lowered = clean.lower()
            if lowered.startswith(("tags:", "fonte:", "score:", "##", "nome oficial:", "categoria:", "documento:")):
                continue
            if "—" in clean:
                after = clean.split("—", 1)[1].strip()
                body = re.search(r"\b((?:O|A|Os|As|Um|Uma|Este|Esta|A\s+Smart|A\s+Grani)\b.+)$", after)
                if body:
                    clean = body.group(1).strip()
                else:
                    left = clean.split("—", 1)[0].strip()
                    if len(left.split()) <= 6 and not after.endswith((".", "!", "?")):
                        continue
                    clean = after
            if len(clean) < 35:
                continue
            lowered = clean.lower()
            if lowered.startswith(TITLE_PREFIXES) or any(marker in lowered for marker in META_MARKERS):
                continue
            key = normalize_text(clean)[:100]
            if key in seen or any(key[:60] in existing or existing[:60] in key for existing in seen):
                continue
            seen.add(key)
            sentences.append(clean.rstrip("."))
            if len(sentences) >= max_sentences:
                break
        if len(sentences) >= max_sentences:
            break
    if not sentences:
        return ""
    synthesized = ". ".join(sentences).strip()
    if synthesized and not synthesized.endswith((".", "!", "?")):
        synthesized += "."
    if re.match(r"^[A-Za-z]/[A-Za-z]", synthesized):
        synthesized = re.sub(r"^.*?([A-ZÁÉÍÓÚÂÊÔÃÕÇ].+)$", r"\1", synthesized)
    return synthesized[:max_chars]
