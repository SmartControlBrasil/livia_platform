from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from assistant_core.prompts.livia import DEFAULT_REPLY
from assistant_core.state import LeadState

NAME_DEFERRED_PATTERNS = (
    r"\bnao quero (falar|informar|passar|dar)\b.{0,40}\bnome\b",
    r"\bnao quero falar meu nome\b",
    r"\bprefiro nao informar\b",
    r"\bprefiro nao (falar|passar|dar) (meu )?nome\b",
    r"\bdepois eu passo (o |meu )?nome\b",
    r"\bpodemos continuar sem (o |meu )?nome\b",
    r"\bsem (o |meu )?nome por agora\b",
    r"\bainda nao quero (dar|falar|informar) (o |meu )?nome\b",
    r"\bagora nao( quero)? (falar|informar|passar) (o |meu )?nome\b",
)

ENRICHMENT_PHRASES = (
    "trabalho com",
    "trabalhamos com",
    "minha loja",
    "meu negocio",
    "nossa loja",
    "produtos para",
    "produto para",
    "clientes de",
)
ENRICHMENT_WORDS = (
    "vendo",
    "atendo",
    "atendemos",
    "comercializo",
    "produzo",
    "fabrico",
    "catalogo",
    "segmento",
)

ENRICHMENT_CAPTURE_PATTERNS = (
    r"(?:vendo|comercializo|fabrico|produzo)\s+(.+)$",
    r"(?:trabalho com|trabalhamos com|atendo|atendemos)\s+(.+)$",
    r"(?:produtos para|produto para|loja (?:de|para)|catalogo de|catálogo de)\s+(.+)$",
    r"(?:minha loja vende|nossa loja vende|a loja vende)\s+(.+)$",
)

QUESTION_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("timeline", ("quanto tempo", "qual o prazo", "qual prazo", "prazo para", "demora", "demora para", "leva quanto")),
    ("price", ("quanto custa", "qual o preco", "qual o preço", "qual valor", "preco", "preço")),
    ("payment", ("pagamento", "pagar online", "checkout", "meio de pagamento", "pagamento online")),
    ("shipping", ("frete", "calculo de frete", "cálculo de frete", "calcular frete")),
    ("mobile", ("celular", "smartphone", "mobile", "responsiv", "funciona no celular")),
    ("catalog", ("cadastrar produto", "cadastrar meus produto", "catalogo", "catálogo")),
    ("maintenance", ("manutencao depois", "manutenção depois", "fazem manutencao", "fazem manutenção")),
    ("how_it_works", ("como funciona", "como e o processo", "como é o processo")),
)

STOPWORDS = {
    "a", "ao", "as", "com", "da", "das", "de", "do", "dos", "e", "em", "eu", "me",
    "o", "os", "para", "por", "que", "um", "uma", "uns", "umas",
}

NAME_DEFERRED_KEY = "name_deferred"


def normalize_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", normalized)


class TurnKind(str, Enum):
    NAME_DEFERRED = "name_deferred"
    DIRECT_QUESTION = "direct_question"
    NEED_ENRICHMENT = "need_enrichment"
    OTHER = "other"


@dataclass(frozen=True)
class ConversationTurn:
    kind: TurnKind
    question_type: str = ""
    enrichment_snippet: str = ""
    continue_commercial_thread: bool = False


def is_name_deferred(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in NAME_DEFERRED_PATTERNS)


def is_direct_question(text: str) -> bool:
    return bool(detect_question_type(text))


def detect_question_type(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""
    looks_like_question = (
        "?" in str(text or "")
        or normalized.startswith(("quanto ", "qual ", "quais ", "como ", "tem ", "voces ", "voce ", "da para", "da pra", "e possivel", "possivel"))
    )
    if not looks_like_question:
        return ""
    for question_type, markers in QUESTION_TYPES:
        if any(marker in normalized for marker in markers):
            return question_type
    return ""


def is_need_enrichment(text: str) -> bool:
    if is_name_deferred(text) or is_direct_question(text):
        return False
    normalized = normalize_text(text)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in ENRICHMENT_PHRASES):
        return True
    if any(re.search(rf"\b{re.escape(word)}\b", normalized) for word in ENRICHMENT_WORDS):
        return True
    content_words = [word for word in normalized.split() if word not in STOPWORDS and len(word) > 2]
    return len(content_words) >= 3 and not re.search(r"\b(?:meu nome|telefone|whatsapp|email|e-mail)\b", normalized)


def extract_enrichment_snippet(text: str) -> str:
    raw = " ".join(str(text or "").strip().split())
    normalized = normalize_text(raw)
    for pattern in ENRICHMENT_CAPTURE_PATTERNS:
        match = re.search(pattern, normalized)
        if match:
            snippet = match.group(1).strip(" .;,-")
            snippet = re.split(r"\b(?:e preciso|quero|gostaria)\b", snippet)[0].strip()
            if 2 <= len(snippet) <= 80:
                return snippet
    return ""


def merge_need_summaries(existing: str, incoming: str) -> str:
    current = " ".join(str(existing or "").strip().split())
    addition = " ".join(str(incoming or "").strip().split())
    if not addition:
        return current[:500]
    if not current:
        return addition[:500]
    if normalize_text(addition) in normalize_text(current):
        return current[:500]
    merged = f"{current.rstrip('. ')}. {addition}"
    return merged[:500]


def _lead_model_has_qualification_data(lead_draft) -> bool:
    meta = getattr(getattr(lead_draft, "__class__", None), "_meta", None)
    if meta is None:
        return False
    try:
        meta.get_field("qualification_data")
        return True
    except Exception:
        return False


def lead_has_name_deferred(lead_draft) -> bool:
    if lead_draft is None:
        return False
    if _lead_model_has_qualification_data(lead_draft):
        data = getattr(lead_draft, "qualification_data", None) or {}
        if isinstance(data, dict) and data.get(NAME_DEFERRED_KEY):
            return True
    conversation = getattr(lead_draft, "conversation", None)
    if conversation is None:
        return False
    try:
        messages = conversation.messages.all()
    except Exception:
        return False
    for message in messages:
        role = str(getattr(message, "role", "") or "")
        content = str(getattr(message, "content", "") or "")
        if role == "user" and is_name_deferred(content):
            return True
    return False


def mark_name_deferred(lead_draft) -> None:
    if lead_draft is None or not _lead_model_has_qualification_data(lead_draft):
        return
    data = dict(getattr(lead_draft, "qualification_data", None) or {})
    if data.get(NAME_DEFERRED_KEY) is True:
        return
    data[NAME_DEFERRED_KEY] = True
    lead_draft.qualification_data = data
    update_fields = ["qualification_data"]
    if hasattr(lead_draft, "updated_at"):
        update_fields.append("updated_at")
    lead_draft.save(update_fields=update_fields)


def has_prior_commercial_thread(conversation, history) -> bool:
    lead_state = str(getattr(conversation, "lead_state", "") or "")
    if lead_state in {LeadState.COLLECT_NEED, LeadState.COLLECT_NAME_COMPANY, LeadState.COLLECT_CONTACT, LeadState.OFFER_HANDOFF}:
        return True
    lead = _conversation_lead(conversation)
    if lead is not None and (str(getattr(lead, "need_summary", "") or "").strip() or lead_has_name_deferred(lead)):
        return True
    for item in history or []:
        if str(item.get("role") or "") != "user":
            continue
        content = normalize_text(item.get("content") or "")
        if any(marker in content for marker in ("preciso", "quero", "gostaria", "orcamento", "orçamento", "loja", "site", "sistema", "automacao", "automação")):
            return True
    return False


def has_open_commercial_thread(conversation, history, discovery) -> bool:
    if has_prior_commercial_thread(conversation, history):
        return True
    if discovery is not None and (getattr(discovery, "has_commercial_interest", False) or getattr(discovery, "has_quote_request", False)):
        if getattr(discovery, "intent", "") != "greeting":
            return True
    return False


def classify_conversation_turn(*, current_message: str, history, conversation, discovery) -> ConversationTurn:
    from assistant_core.consultative_policy import is_explicit_collection_trigger

    thread = has_prior_commercial_thread(conversation, history)
    if is_explicit_collection_trigger(current_message):
        # Explicit budget/hire/human handoff must reach the qualification path.
        return ConversationTurn(kind=TurnKind.OTHER, continue_commercial_thread=True)
    if is_name_deferred(current_message):
        return ConversationTurn(kind=TurnKind.NAME_DEFERRED, continue_commercial_thread=True)
    question_type = detect_question_type(current_message)
    if question_type:
        return ConversationTurn(
            kind=TurnKind.DIRECT_QUESTION,
            question_type=question_type,
            continue_commercial_thread=True,
        )
    if thread and is_need_enrichment(current_message):
        return ConversationTurn(
            kind=TurnKind.NEED_ENRICHMENT,
            enrichment_snippet=extract_enrichment_snippet(current_message),
            continue_commercial_thread=True,
        )
    return ConversationTurn(kind=TurnKind.OTHER, continue_commercial_thread=thread)


def build_name_deferred_reply(lead_draft=None) -> str:
    need = str(getattr(lead_draft, "need_summary", "") or "").strip()
    follow_up = _next_discovery_question(need, snippet="")
    return f"Tudo bem, podemos seguir sem o nome por agora. {follow_up}"


def build_enrichment_reply(lead_draft=None, *, snippet: str = "", current_message: str = "") -> str:
    need = str(getattr(lead_draft, "need_summary", "") or "").strip() or str(current_message or "").strip()
    detail = snippet or extract_enrichment_snippet(current_message)
    if detail and need:
        return (
            f"Entendi. Anotei que isso envolve {detail}, no contexto de { _short_need(need) }. "
            f"{_catalog_or_scope_question(need)}"
        )
    if detail:
        return f"Entendi, você trabalha com {detail}. {_catalog_or_scope_question(need)}"
    return f"Entendi, isso ajuda a detalhar a necessidade. {_catalog_or_scope_question(need)}"


def build_direct_question_reply(lead_draft=None, *, question_type: str, current_message: str = "") -> str:
    need = str(getattr(lead_draft, "need_summary", "") or "").strip()
    answer = _grounded_question_answer(question_type, need)
    follow_up = _follow_up_after_question(question_type, need)
    return f"{answer} {follow_up}".strip()


def skip_name_prompt_fields(missing_fields: list[str], lead_draft, *, current_message: str = "") -> list[str]:
    fields = list(missing_fields or [])
    if "name_or_company" not in fields:
        return fields
    if lead_has_name_deferred(lead_draft) or is_name_deferred(current_message) or is_direct_question(current_message) or is_need_enrichment(current_message):
        return [field for field in fields if field != "name_or_company"]
    return fields


def _conversation_lead(conversation):
    if conversation is None:
        return None
    try:
        return conversation.lead_draft
    except Exception:
        return None


def _short_need(need: str) -> str:
    cleaned = " ".join(str(need or "").strip().split())
    if not cleaned:
        return "sua necessidade"
    if len(cleaned) <= 80:
        return cleaned.rstrip(".")
    return cleaned[:77].rstrip() + "…"


def _catalog_or_scope_question(need: str) -> str:
    normalized = normalize_text(need)
    if any(marker in normalized for marker in ("loja", "ecommerce", "e-commerce", "virtual", "site", "catalogo", "catálogo", "produto")):
        return "Você pretende começar com poucos produtos ou já tem um catálogo maior?"
    if any(marker in normalized for marker in ("sistema", "portal", "dashboard", "aplicativo", "app")):
        return "Quais processos esse sistema precisa cobrir primeiro?"
    if any(marker in normalized for marker in ("automacao", "automação", "robo", "robô", "robotica", "robótica")):
        return "Qual processo você quer automatizar primeiro?"
    return "Qual detalhe é mais importante para você neste momento: escopo, prazo ou forma de operação?"


def _next_discovery_question(need: str, snippet: str) -> str:
    if snippet:
        return _catalog_or_scope_question(f"{need} {snippet}")
    return _catalog_or_scope_question(need)


def _grounded_question_answer(question_type: str, need: str) -> str:
    if question_type == "timeline":
        return (
            "O prazo depende principalmente da quantidade de produtos, meios de pagamento, "
            "frete, integrações e conteúdo. Posso levantar esses pontos com você para chegar a uma estimativa mais precisa."
        )
    if question_type == "price":
        return "O investimento varia conforme o escopo, volume e integrações envolvidas. Ainda não tenho um valor fechado para informar aqui."
    if question_type == "payment":
        return "Pagamento online pode entrar no projeto, mas o detalhe depende do checkout e dos meios que você precisa usar."
    if question_type == "shipping":
        return "Cálculo de frete também pode fazer parte do projeto, conforme a operação logística que você já usa ou pretende usar."
    if question_type == "mobile":
        return "O acesso em celular costuma fazer parte desse tipo de projeto, e o desenho final depende de como as pessoas vão usar."
    if question_type == "catalog":
        return "Sim, o cadastro de produtos é um ponto central. O esforço muda bastante se o catálogo começa pequeno ou já chega grande."
    if question_type == "maintenance":
        return "A manutenção depois da entrega depende do que for combinado no projeto. Posso registrar essa necessidade para a equipe detalhar."
    if question_type == "how_it_works":
        return "O caminho usual é entender a necessidade, organizar o escopo e só então estimar prazo e próximas etapas."
    return "Posso te orientar com o que já temos da necessidade, sem fechar condição comercial daqui."


def _follow_up_after_question(question_type: str, need: str) -> str:
    if question_type in {"timeline", "price", "catalog", "payment", "shipping"}:
        return _catalog_or_scope_question(need)
    return "O que é mais urgente para você neste momento?"


def is_generic_fallback_reply(reply: str) -> bool:
    return normalize_text(reply) == normalize_text(DEFAULT_REPLY)
