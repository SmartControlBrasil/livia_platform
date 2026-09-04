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
    "galpao", "galpão", "armazem", "armazém", "concreto", "porcelanato", "epoxi", "epóxi",
)


def normalize_text(value: str) -> str:
    normalized = str(value or "").strip().lower().replace(BOM, "")
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", normalized)


TECHNICAL_REQUIREMENT_TERMS = (
    ("bncc", "atendimento à BNCC"),
    ("certificado", "certificação"),
    ("certificacao", "certificação"),
    ("certificação", "certificação"),
    ("nasa", "certificação ou relação com a NASA"),
    ("autonomia", "autonomia"),
    ("bateria", "bateria"),
    ("recarga", "recarga"),
    ("carrega", "recarga"),
    ("carregar", "recarga"),
    ("tomada", "alimentação/recarga"),
    ("sensor", "sensores"),
    ("capacidade", "capacidade"),
    ("tensao", "tensão de alimentação"),
    ("tensão", "tensão de alimentação"),
    ("voltagem", "tensão de alimentação"),
    ("garantia", "garantia"),
    ("epoxi", "piso epóxi"),
    ("epóxi", "piso epóxi"),
    ("porcelanato", "porcelanato"),
    ("ip67", "certificação IP67"),
    ("peso", "peso operacional"),
    ("pesa", "peso operacional"),
)


def _unsupported_requirement_reply(knowledge_context: str, *, current_message: str = "") -> str:
    msg = normalize_text(current_message)
    if not msg:
        return ""
    kb_content = _knowledge_content_text(knowledge_context)
    kb = normalize_text(kb_content)
    if not kb:
        return ""
    missing_labels = []
    for term, label in TECHNICAL_REQUIREMENT_TERMS:
        if "nasa" in msg and label == "certificação":
            continue
        if term in msg and (_term_missing_from_kb(term, kb) or _term_marked_not_documented(kb_content, term)) and label not in missing_labels:
            missing_labels.append(label)
    if not missing_labels:
        return ""

    supported = synthesize_deterministic_reply(
        knowledge_context,
        base_reply="",
        max_sentences=1,
        current_message="visão geral do item mencionado",
        active_application="",
    )
    missing = ", ".join(missing_labels[:2])
    if supported:
        return f"A documentação disponível confirma: {supported} Mas não encontrei confirmação sobre {missing} na documentação recuperada."
    return f"Não encontrei confirmação sobre {missing} na documentação disponível."


def _direct_technical_fact_reply(knowledge_context: str, *, current_message: str = "") -> str:
    msg_n = normalize_text(current_message)
    kb_text = _knowledge_content_text(knowledge_context)
    if not msg_n or not kb_text.strip():
        return ""
    wanted: tuple[str, ...] = ()
    if any(token in msg_n for token in ("autonomia", "bateria", "dura", "duracao", "duração")):
        wanted = ("autonomia", "bateria", "horas")
    elif any(token in msg_n for token in ("tensao", "tensão", "voltagem", "carrega", "carregar", "estacao", "estação")):
        wanted = ("tensao", "tensão", "voltagem", "alimentacao", "alimentação", " v")
    elif any(token in msg_n for token in ("peso", "pesa")):
        wanted = ("peso", " kg")
    elif any(token in msg_n for token in ("circulando", "pessoas", "fluxo", "transito", "trânsito", "movimento", "noite", "horario", "horário")):
        wanted = ("fluxo", "pessoas", "horario", "horário", "circula", "operacao", "operação")
    if not wanted:
        return ""
    candidates = []
    cleaned = re.sub(r"#+\s*", " ", kb_text)
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", cleaned):
        sentence = " ".join(sentence.split()).strip(" -•#")
        if len(sentence) < 8:
            continue
        sentence_n = normalize_text(sentence)
        weight = sum(1 for token in wanted if token.strip() and token in sentence_n)
        if weight:
            candidates.append((weight, sentence))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (-item[0], len(item[1])))
    return strip_meta_rag_phrasing(candidates[0][1])


def _term_missing_from_kb(term: str, kb: str) -> bool:
    term_n = normalize_text(term)
    if term_n in {"bateria", "dura", "duração", "duracao"} and "autonomia" in kb:
        return False
    if term_n in {"pesa", "peso"} and "peso" in kb:
        return False
    if term_n in {"tensao", "tensão", "voltagem", "carrega", "carregar", "tomada"} and any(token in kb for token in ("tensao", "tensão", "voltagem", "alimentacao", "alimentação", "220 v", "110 v", "380 v")):
        return False
    return term_n not in kb


def _term_marked_not_documented(kb_content: str, term: str) -> bool:
    term_n = normalize_text(term)
    for line in str(kb_content or "").splitlines():
        line_n = normalize_text(line)
        if term_n not in line_n:
            continue
        if any(marker in line_n for marker in ("nao documentado", "não documentado", "nao afirmar", "não afirmar", "sem documentacao", "sem documentação")):
            return True
    return False


def _knowledge_content_text(knowledge_context: str) -> str:
    text = str(knowledge_context or "")
    if "[KNOWLEDGE_BASE]" not in text.upper():
        return text
    parts: list[str] = []
    capture = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        upper = line.upper()
        if upper.startswith(("[KNOWLEDGE_BASE]", "[/KNOWLEDGE_BASE]")):
            capture = False
            continue
        if lowered.startswith(("fonte:", "referência:", "referencia:", "score:")):
            capture = False
            continue
        if lowered.startswith(("conteúdo:", "conteudo:")):
            capture = True
            remainder = line.split(":", 1)[1].strip()
            if remainder:
                parts.append(remainder)
            continue
        if capture and line:
            parts.append(line)
    return chr(10).join(parts) if parts else text


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
    current_message: str = "",
    active_domain: str = "",
    active_application: str = "",
) -> str:
    shape = detect_answer_shape(current_message)
    unsupported_reply = _unsupported_requirement_reply(knowledge_context, current_message=current_message)
    if unsupported_reply:
        return unsupported_reply
    direct_fact = _direct_technical_fact_reply(knowledge_context, current_message=current_message)
    if direct_fact:
        return direct_fact
    hints = _extract_safe_bits(knowledge_context)
    hints = _select_primary_bits(
        hints,
        current_message=current_message,
        active_domain=active_domain,
        active_application=active_application,
    )
    hints = _prioritize_answer_sentences(hints, current_message=current_message)
    synthesized = _sentences_from_bits(hints, max_sentences=max_sentences, max_chars=max_chars)
    synthesized = strip_meta_rag_phrasing(synthesized)
    synthesized = _apply_answer_shape(synthesized, shape=shape, current_message=current_message, active_application=active_application)
    base = str(base_reply or "").strip()
    if not synthesized:
        return strip_meta_rag_phrasing(base)
    if not base or is_generic_fallback_reply(base):
        return synthesized
    # Preço conceitual / policy comercial: resposta de preço prevalece (não inventar ficha + preço).
    base_n = normalize_text(base)
    if any(token in base_n for token in ("investimento varia", "valor fechado", "nao tenho um valor", "não tenho um valor")):
        return strip_meta_rag_phrasing(base)
    if normalize_text(synthesized) in normalize_text(base):
        return strip_meta_rag_phrasing(base)
    return strip_meta_rag_phrasing(f"{synthesized}\n\n{base}")


def detect_answer_shape(message: str) -> str:
    normalized = normalize_text(message)
    if not normalized:
        return "GENERAL"
    if any(token in normalized for token in ("quanto custa", "qual o preco", "qual o preço", "qual valor")):
        return "PRICE"
    if any(token in normalized for token in ("melhor", "recomenda", "indic", "suger")):
        return "RECOMMENDATION"
    if any(token in normalized for token in ("como funciona", "como e o processo", "como é o processo")):
        return "PROCESS"
    if any(token in normalized for token in ("trabalham com", "voces fazem", "vocês fazem", "voces tem", "vocês têm")):
        return "CAPABILITY"
    if any(token in normalized for token in ("o que e", "o que é", "fale sobre", "me fale", "me fala")):
        return "WHAT_IS"
    return "GENERAL"


def strip_meta_rag_phrasing(text: str) -> str:
    cleaned = str(text or "")
    patterns = (
        r"(?i)\bO site cita\b",
        r"(?i)\bO site informa\b",
        r"(?i)\bO site orienta que\b",
        r"(?i)\bO site descreve\b",
        r"(?i)\bHá referências a\b",
        r"(?i)\bHa referencias a\b",
        r"(?i)\bEncontramos informações sobre\b",
        r"(?i)\bEncontramos informacoes sobre\b",
        r"(?i)\bO contexto recuperado indica\b",
        r"(?i)\bFonte: página oficial[^.]*\.\s*",
        r"(?i)\bFonte: pagina oficial[^.]*\.\s*",
    )
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;:-")
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def _strip_site_meta_phrasing(text: str) -> str:
    return strip_meta_rag_phrasing(text)


def _select_primary_bits(
    hints: list[str],
    *,
    current_message: str = "",
    active_domain: str = "",
    active_application: str = "",
) -> list[str]:
    if not hints:
        return []
    msg_n = normalize_text(current_message)
    app = normalize_text(active_application)
    domain = normalize_text(active_domain)
    scored: list[tuple[int, str]] = []
    for hint in hints:
        hint_n = normalize_text(hint)
        weight = 0
        if domain == "automation" and any(token in hint_n for token in ("mitsubishi", "clp", "automacao")):
            weight += 3
        if domain == "automation" and any(token in hint_n for token in ("xyron", "limpeza", "escola", "duno")):
            weight -= 4
        if app in {"kitchen_countertop", "cooktop_countertop"}:
            if any(token in hint_n for token in ("cozinha", "cooktop", "bancada", "material", "granito", "marmore", "melhor")):
                weight += 3
            if "gourmet" in hint_n and "cozinha" not in hint_n:
                weight -= 3
        if app == "educational_robotics" or ("escola" in msg_n and "limpeza" not in msg_n):
            if any(token in hint_n for token in ("liro", "educacional", "escola")):
                weight += 3
            if any(token in hint_n for token in ("limpeza", "duno", "mitsubishi")):
                weight -= 3
        if app == "cleaning_robotics" or ("limpeza" in msg_n and "escola" not in msg_n):
            if any(token in hint_n for token in ("limpeza", "duno", "dune", "hygibot", "lavar", "varrer", "aspirar", "galpao", "galpão", "facilities")):
                weight += 3
            if any(token in hint_n for token in ("liro", "educacional", "escola", "crianca", "criança")):
                weight -= 4
            if any(
                token in hint_n
                for token in (
                    "trabalha com robotica de servico",
                    "trabalha com robótica de serviço",
                    "linha xyron robotics",
                    "visao geral",
                    "visão geral",
                    "quais robos",
                    "quais robôs",
                )
            ) and not any(token in hint_n for token in ("hygibot", "dune", "duno", "limpeza profissional")):
                weight -= 6
        # lexical overlap
        overlap = sum(1 for token in msg_n.split() if len(token) > 3 and token in hint_n)
        weight += min(overlap, 3)
        scored.append((weight, hint))
    scored.sort(key=lambda item: item[0], reverse=True)
    primary = [hint for weight, hint in scored if weight >= 0][:2]
    return primary or [scored[0][1]]


def _prioritize_answer_sentences(hints: list[str], *, current_message: str = "") -> list[str]:
    msg_n = normalize_text(current_message)
    if not hints or not msg_n:
        return hints
    wanted: tuple[str, ...] = ()
    if any(token in msg_n for token in ("autonomia", "bateria", "dura", "duracao", "duração")):
        wanted = ("autonomia", "bateria", "horas")
    elif any(token in msg_n for token in ("tensao", "tensão", "voltagem", "carrega", "carregar", "estacao", "estação")):
        wanted = ("tensao", "tensão", "voltagem", "alimentacao", "alimentação", "220 v", "380 v")
    elif any(token in msg_n for token in ("peso", "pesa")):
        wanted = ("peso", "kg")
    elif any(token in msg_n for token in ("circulando", "pessoas", "fluxo", "transito", "trânsito", "movimento", "noite", "horario", "horário")):
        wanted = ("fluxo", "pessoas", "horario", "horário", "circula", "operacao", "operação")
    elif any(token in msg_n for token in ("garantia", "certificacao", "certificação", "ip67")):
        wanted = ("garantia", "certificacao", "certificação", "ip67")
    if not wanted:
        return hints
    sentences: list[str] = []
    for hint in hints:
        sentences.extend(part.strip() for part in re.split(r"(?<=[.!?])\s+", hint) if part.strip())
    ranked = sorted(
        sentences,
        key=lambda sentence: sum(1 for token in wanted if token in normalize_text(sentence)),
        reverse=True,
    )
    return ranked or hints


def _apply_answer_shape(
    synthesized: str,
    *,
    shape: str,
    current_message: str = "",
    active_application: str = "",
) -> str:
    text = str(synthesized or "").strip()
    if not text:
        return text
    if shape == "RECOMMENDATION":
        app = normalize_text(active_application)
        if app in {"kitchen_countertop", "cooktop_countertop", "gourmet_countertop", "bathroom_countertop"}:
            if not any(token in normalize_text(text) for token in ("depende", "compar", "nao existe melhor", "não existe melhor")):
                prefix = (
                    "Não existe um material universalmente melhor: a escolha depende da aplicação e do uso. "
                )
                if prefix.lower() not in text.lower():
                    text = f"{prefix}{text}"
        elif not app and is_ambiguous_material_question(current_message):
            return "Você está comparando quais opções, ou para qual aplicação (cozinha, banheiro, gourmet)?"
    return text


def is_ambiguous_material_question(message: str) -> bool:
    from assistant_core.dialogue_memory import is_material_recommendation_question

    return is_material_recommendation_question(message)


def consultative_followup_for_context(need_or_message: str) -> str:
    """Compat: delega para a estratégia centralizada."""
    from assistant_core.dialogue_memory import DialogueMemory, infer_application, infer_domain, infer_topic
    from assistant_core.followup_strategy import select_followup

    memory = DialogueMemory(
        active_domain=infer_domain(need_or_message),
        active_topic=infer_topic(need_or_message),
        active_application=infer_application(need_or_message),
    )
    follow, _ = select_followup(memory=memory, current_message=need_or_message, force=True)
    return follow


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
    def _safe_fallback() -> str:
        if active_entity:
            return f"Posso te orientar sobre {active_entity}."
        return DEFAULT_REPLY

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
        return _safe_fallback()
    if context_blob:
        follow = str(consultative_followup_for_context(context_blob) or "").strip()
        # Fail-closed: mensagem sem domínio conhecido não pode virar reply vazia.
        return follow or _safe_fallback()
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
    capital = re.search(r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ].{11,}", clipped)
    if capital:
        clipped = capital.group(0).strip()
    clipped = re.sub(r"`?templates/institutional/[^`\s]+`?", "", clipped)
    clipped = re.sub(r"\s+", " ", clipped).strip()
    # Descarta cortes de título com barra/categoria antes do corpo público.
    if re.match(r"^[A-Za-zÀ-ÿ]?\s*/\s*[A-Za-z]", clipped) or "categoria:" in clipped.lower()[:40]:
        body = re.search(r"\b((?:Robô|Robo|Indicar|Atendemos|Desenvolvemos|Integra|Apoia|Apoiar|[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÀ-ÿ0-9-]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]?[A-Za-zÀ-ÿ0-9-]+){0,4})\b.+)$", clipped)
        clipped = body.group(1).strip() if body else ""
    clipped = clipped[:280].strip(" -•#")
    if len(clipped) < 12:
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
            if len(clean) < 12:
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
            if len(clean) < 12:
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
