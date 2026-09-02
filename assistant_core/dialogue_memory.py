"""Memória determinística de domínio/entidade para diálogo consultivo multi-turno."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from assistant_core.conversation_turns import normalize_text

ACTIVE_ENTITY_KEY = "active_entity"
ACTIVE_DOMAIN_KEY = "active_domain"
ACTIVE_TOPIC_KEY = "active_topic"
ACTIVE_NEED_KEY = "active_need"
CONTACT_DEFERRED_KEY = "contact_collection_deferred"
COMMERCIAL_INTENT_KEY = "commercial_intent"
COLLECTION_PAUSED_KEY = "collection_paused"

# Aliases derivados de corpus/documentação conhecida (não hardcode de state machine).
# Equivalências só quando documentação confirma (HygiBot/Dune; "Duno" = typo comum de Dune).
ENTITY_REGISTRY: tuple[dict, ...] = (
    {
        "canonical": "Duno",
        "aliases": ("duno", "dune", "hygibot dune", "hygibot duno", "hygibot", "dune bot", "duno bot"),
        "domain": "robotics",
        "topic": "cleaning_robot",
        "keywords": ("limpeza", "lavar", "varrer", "aspirar", "passar pano"),
    },
    {
        "canonical": "LIRO",
        "aliases": ("liro", "littlebot", "little bot"),
        "domain": "robotics",
        "topic": "educational_robot",
        "keywords": ("educacional", "crianca", "criança", "escola"),
    },
    {
        "canonical": "NeoBot",
        "aliases": ("neobot", "neo bot"),
        "domain": "robotics",
        "topic": "service_robot",
        "keywords": ("recepcao", "recepção", "atendimento"),
    },
    {
        "canonical": "Orbit",
        "aliases": ("orbit",),
        "domain": "robotics",
        "topic": "service_robot",
        "keywords": (),
    },
    {
        "canonical": "Buddy",
        "aliases": ("buddy",),
        "domain": "robotics",
        "topic": "service_robot",
        "keywords": (),
    },
    {
        "canonical": "Mitsubishi",
        "aliases": ("mitsubishi", "clp mitsubishi", "ihm mitsubishi"),
        "domain": "automation",
        "topic": "industrial_automation",
        "keywords": ("clp", "ihm", "automacao", "automação"),
    },
)

DOMAIN_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("materials", ("bancada", "granito", "marmore", "mármore", "cooktop", "pia", "ilha", "nicho", "cuba", "escada", "gourmet", "cozinha", "banheiro", "lavabo")),
    ("robotics", ("robo", "robô", "robotica", "robótica", "limpeza", "xyron", "hygibot", "duno", "dune", "liro", "neobot")),
    ("automation", ("automacao", "automação", "mitsubishi", "clp", "ihm", "manutencao", "manutenção")),
    ("software_web", ("python", "sistema web", "loja virtual", "ecommerce", "e-commerce", "site ", " website", "portal")),
    ("maintenance", ("manutencao tecnica", "manutenção técnica", "pecas", "peças", "suporte tecnico", "suporte técnico")),
)

PRONOUN_MARKERS = (
    r"\b(ele|ela|esse|essa|isto|isso|este|esta)\b",
    r"\besse (robo|robô|modelo|produto|sistema|material)\b",
    r"\beste (robo|robô|modelo|produto|sistema|material)\b",
    r"\bo (robo|robô|modelo|produto)\b",
)

CONTACT_DEFERRED_PATTERNS = (
    r"\bnao quero (passar|informar|dar|falar)\b.{0,40}\b(contato|telefone|whatsapp|e-?mail|email|celular)\b",
    r"\bprefiro nao (passar|informar|dar)\b.{0,40}\b(contato|telefone|whatsapp|e-?mail|email)\b",
    r"\bnao quero passar contato\b",
    r"\bdepois (eu )?(passo|envio|mando) (meu |os )?dados\b",
    r"\bprefiro tirar minhas duvidas\b",
    r"\btire minhas duvidas\b",
    r"\btira minhas duvidas\b",
    r"\bsem (passar |dar )?(contato|telefone|dados) por agora\b",
    r"\bagora nao( quero)? (passar|informar)\b.{0,30}\b(telefone|contato|email|e-mail|whatsapp)\b",
)

CONTINUE_CONSULTATIVE_MARKERS = (
    "tire minhas duvidas",
    "tira minhas duvidas",
    "prefiro tirar minhas duvidas",
    "quero tirar duvidas",
    "so quero tirar duvidas",
    "só quero tirar dúvidas",
)


@dataclass
class DialogueMemory:
    active_entity: str = ""
    active_domain: str = ""
    active_topic: str = ""
    active_need: str = ""
    contact_collection_deferred: bool = False
    commercial_intent: bool = False
    collection_paused: bool = False
    entity_match: bool = False
    domain_match: bool = False
    retrieval_query_original: str = ""
    retrieval_query_contextual: str = ""
    notes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            ACTIVE_ENTITY_KEY: self.active_entity,
            ACTIVE_DOMAIN_KEY: self.active_domain,
            ACTIVE_TOPIC_KEY: self.active_topic,
            ACTIVE_NEED_KEY: self.active_need,
            CONTACT_DEFERRED_KEY: self.contact_collection_deferred,
            COMMERCIAL_INTENT_KEY: self.commercial_intent,
            COLLECTION_PAUSED_KEY: self.collection_paused,
        }

    def observability(self) -> dict:
        return {
            "active_entity": self.active_entity,
            "active_domain": self.active_domain,
            "active_topic": self.active_topic,
            "entity_match": self.entity_match,
            "domain_match": self.domain_match,
            "contextual_query_used": bool(
                self.retrieval_query_contextual
                and self.retrieval_query_contextual.strip()
                and self.retrieval_query_contextual.strip() != (self.retrieval_query_original or "").strip()
            ),
            "contact_collection_deferred": self.contact_collection_deferred,
            "collection_paused": self.collection_paused,
            "commercial_intent": self.commercial_intent,
        }


def load_dialogue_memory(conversation=None, lead_draft=None) -> DialogueMemory:
    lead = lead_draft
    if lead is None and conversation is not None:
        try:
            lead = conversation.lead_draft
        except Exception:
            lead = None
    data = {}
    if lead is not None:
        raw = getattr(lead, "qualification_data", None) or {}
        if isinstance(raw, dict):
            data = raw
    return DialogueMemory(
        active_entity=str(data.get(ACTIVE_ENTITY_KEY) or ""),
        active_domain=str(data.get(ACTIVE_DOMAIN_KEY) or ""),
        active_topic=str(data.get(ACTIVE_TOPIC_KEY) or ""),
        active_need=str(data.get(ACTIVE_NEED_KEY) or getattr(lead, "need_summary", "") or ""),
        contact_collection_deferred=bool(data.get(CONTACT_DEFERRED_KEY)),
        commercial_intent=bool(data.get(COMMERCIAL_INTENT_KEY)),
        collection_paused=bool(data.get(COLLECTION_PAUSED_KEY)),
    )


def persist_dialogue_memory(lead_draft, memory: DialogueMemory) -> None:
    if lead_draft is None or not hasattr(lead_draft, "qualification_data"):
        return
    # Evita sobrescrever flags gravadas por outro caminho na mesma request (coleta).
    try:
        lead_draft.refresh_from_db(fields=["qualification_data"])
    except Exception:
        pass
    data = dict(getattr(lead_draft, "qualification_data", None) or {})
    preserved_active = bool(data.get("collection_active"))
    payload = memory.to_dict()
    if preserved_active and not memory.collection_paused:
        # Coleta explícita aberta: não reaplicar pause/deferência da memória em memória stale.
        payload.pop(COLLECTION_PAUSED_KEY, None)
        payload.pop(CONTACT_DEFERRED_KEY, None)
    data.update(payload)
    if preserved_active and not memory.collection_paused:
        data["collection_active"] = True
        data[COLLECTION_PAUSED_KEY] = False
    lead_draft.qualification_data = data
    update_fields = ["qualification_data"]
    if hasattr(lead_draft, "updated_at"):
        update_fields.append("updated_at")
    lead_draft.save(update_fields=update_fields)


def is_contact_deferred(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in CONTACT_DEFERRED_PATTERNS)


def wants_consultative_continue(text: str) -> bool:
    normalized = normalize_text(text)
    return any(marker in normalized for marker in CONTINUE_CONSULTATIVE_MARKERS)


def message_has_pronoun_reference(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in PRONOUN_MARKERS)


def detect_entity_mention(text: str) -> dict | None:
    normalized = normalize_text(text)
    if not normalized:
        return None
    best = None
    best_len = 0
    for entry in ENTITY_REGISTRY:
        for alias in entry["aliases"]:
            alias_n = normalize_text(alias)
            if not alias_n:
                continue
            if re.search(rf"(?<!\w){re.escape(alias_n)}(?!\w)", normalized):
                if len(alias_n) > best_len:
                    best = entry
                    best_len = len(alias_n)
    return best


def infer_domain(text: str, *, fallback: str = "") -> str:
    normalized = normalize_text(text)
    if not normalized:
        return fallback
    for domain, markers in DOMAIN_MARKERS:
        if any(marker in normalized for marker in markers):
            return domain
    return fallback


def update_dialogue_memory_from_turn(
    *,
    memory: DialogueMemory,
    current_message: str,
    history=None,
    need_summary: str = "",
    commercial_trigger: bool = False,
) -> DialogueMemory:
    message = str(current_message or "")
    normalized = normalize_text(message)
    history_blob = " ".join(
        str(item.get("content") or "")
        for item in (history or [])
        if item.get("role") == "user"
    )
    context_blob = " ".join([memory.active_need, need_summary, history_blob, message]).strip()

    entity = detect_entity_mention(message)
    if entity:
        memory.active_entity = entity["canonical"]
        memory.active_domain = entity["domain"]
        memory.active_topic = entity["topic"]
        memory.entity_match = True
    elif message_has_pronoun_reference(message) and memory.active_entity:
        memory.entity_match = True
    else:
        # Troca explícita de assunto sem entidade.
        switched = infer_domain(message)
        if switched and switched != memory.active_domain and not message_has_pronoun_reference(message):
            # Só troca domínio se a mensagem trouxer sinais claros e não for só pronome.
            if any(len(normalize_text(m)) >= 4 for m in [message]):
                if switched in {"software_web", "automation", "materials", "maintenance", "robotics"}:
                    # Evita resetar entity em mensagens curtas de continuidade.
                    strong_switch = any(
                        token in normalized
                        for token in ("agora", "sobre sistemas", "python", "mitsubishi", "site", "loja virtual", "bancada")
                    )
                    if strong_switch or not memory.active_entity:
                        memory.active_domain = switched
                        if strong_switch and switched != "robotics":
                            memory.active_entity = ""
                            memory.active_topic = ""

    if not memory.active_domain:
        memory.active_domain = infer_domain(context_blob) or infer_domain(message)

    if need_summary:
        memory.active_need = str(need_summary).strip()[:240]
    elif not memory.active_need and normalized:
        if any(token in normalized for token in ("quero", "gostaria", "preciso", "rob", "bancada", "site")):
            memory.active_need = message.strip()[:240]

    if commercial_trigger:
        memory.commercial_intent = True

    if is_contact_deferred(message) or wants_consultative_continue(message):
        memory.contact_collection_deferred = True
        memory.collection_paused = True

    memory.domain_match = bool(memory.active_domain)
    return memory


def build_contextual_retrieval_query(
    *,
    current_message: str,
    memory: DialogueMemory | None = None,
    history=None,
    need_summary: str = "",
) -> tuple[str, str]:
    """Retorna (query_original, query_contextual)."""
    original = str(current_message or "").strip()
    memory = memory or DialogueMemory()
    parts: list[str] = []

    if memory.active_entity:
        parts.append(memory.active_entity)
        # Inclui alias canônico útil para corpus (Duno → HygiBot/Dune).
        entity = next((e for e in ENTITY_REGISTRY if e["canonical"] == memory.active_entity), None)
        if entity:
            if memory.active_entity.lower() in {"duno", "dune"} or normalize_text(memory.active_entity) == "duno":
                parts.extend(["HygiBot", "Dune", "robô de limpeza"])
            parts.extend(list(entity.get("keywords") or [])[:3])

    if memory.active_topic == "cleaning_robot" or "limpeza" in normalize_text(memory.active_need or original):
        parts.append("robô de limpeza")

    if memory.active_domain == "robotics" and "limpeza" in normalize_text(" ".join([memory.active_need, original])):
        parts.append("limpeza grandes áreas")

    if memory.active_domain == "materials":
        parts.append(memory.active_need or "bancada pedras naturais")

    if memory.active_domain == "software_web":
        parts.append("sistemas web")

    if memory.active_domain == "automation":
        parts.append("automação industrial")

    # Últimos turnos user relevantes (não concatena tudo).
    recent_user = [
        str(item.get("content") or "").strip()
        for item in (history or [])[-6:]
        if item.get("role") == "user" and str(item.get("content") or "").strip()
    ]
    for turn in recent_user[-2:]:
        if message_has_pronoun_reference(turn):
            continue
        if len(turn.split()) <= 12:
            parts.append(turn)

    if need_summary:
        parts.append(str(need_summary)[:80])

    # Se a mensagem atual não for só pronome, inclui.
    if original and not (message_has_pronoun_reference(original) and len(original.split()) <= 6):
        parts.append(original)
    elif original:
        parts.append(original)

    # Dedup preservando ordem.
    seen: set[str] = set()
    cleaned: list[str] = []
    for part in parts:
        key = normalize_text(part)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(part.strip())
    contextual = " ".join(cleaned).strip() or original
    return original, contextual


def domain_followup(memory: DialogueMemory, *, force: bool = False) -> str:
    """Follow-up opcional por domínio. Vazio = não anexar pergunta."""
    if not force:
        return ""
    domain = memory.active_domain
    if domain == "robotics" or memory.active_topic == "cleaning_robot":
        return "Para encaixar melhor, qual é o ambiente e o tipo de piso?"
    if domain == "materials":
        return "Você já tem medidas aproximadas ou fotos do ambiente?"
    if domain == "automation":
        return "Qual ambiente e objetivo você quer cobrir primeiro?"
    if domain == "software_web":
        return "O foco é divulgação, captura de contatos, vendas online ou sistema interno?"
    return ""


def should_skip_consultative_followup(*, current_message: str, memory: DialogueMemory | None = None) -> bool:
    """Pedidos diretos de informação sobre entidade/tópico não devem forçar pergunta."""
    normalized = normalize_text(current_message)
    if not normalized:
        return False
    if any(marker in normalized for marker in ("fale sobre", "me fale", "me fala", "conte mais", "mais sobre", "tire minhas", "tira minhas")):
        return True
    if memory and memory.active_entity and message_has_pronoun_reference(current_message):
        return True
    if detect_entity_mention(current_message):
        return True
    return False
