from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Iterable

from django.conf import settings

from assistant_core.discovery import analyze_message
from assistant_core.discovery.contextual import resolve_discovery_question, should_ask_profile_discovery
from assistant_core.services.decision_outcome import has_semantic_knowledge_block, should_combine_kb_with_discovery, is_informational_knowledge_query
from assistant_core.prompts import (
    DEFAULT_REPLY,
    build_contextual_reply,
)
from assistant_core.prompts.livia_ai import build_livia_ai_prompt
from assistant_core.qualification import has_basic_contact
from assistant_core.state import LeadState, can_start_new_cycle, set_state, should_lock_lead
from leads.services import CRMDispatchService, LeadCaptureService
from leads.services.handoff import HandoffService
from integrations.openai.client import OpenAIChatClient
from knowledge_base.rag.context_builder import build_knowledge_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiviaReply:
    intent: str
    reply: str
    handoff_request_id: int | None = None
    handoff_reason: str = ""


@dataclass(frozen=True)
class AssistantProfileContext:
    name: str = "Lívia"
    initial_message: str = "Olá! Sou a Lívia. Como posso te ajudar?"
    tone: str = "consultivo, claro e profissional"
    primary_goal: str = "qualificar leads"
    business_name: str = ""
    business_domain: str = ""
    short_description: str = ""

    @classmethod
    def from_profile(cls, profile, tenant=None) -> "AssistantProfileContext":
        if profile is None:
            return cls()
        tenant_name = str(getattr(tenant, "name", "") or "").strip()
        business_name = str(getattr(profile, "business_name", "") or "").strip() or tenant_name
        return cls(
            name=str(getattr(profile, "name", "") or "Lívia").strip() or "Lívia",
            initial_message=str(
                getattr(profile, "initial_message", "")
                or "Olá! Sou a Lívia. Como posso te ajudar?"
            ).strip(),
            tone=str(getattr(profile, "tone", "") or cls.tone).strip(),
            primary_goal=str(getattr(profile, "primary_goal", "") or cls.primary_goal).strip(),
            business_name=business_name,
            business_domain=str(getattr(profile, "business_domain", "") or "").strip(),
            short_description=str(getattr(profile, "short_description", "") or "").strip(),
        )


class LiviaDecisionService:
    def __init__(
        self,
        lead_capture_service: LeadCaptureService | None = None,
        crm_dispatch_service: CRMDispatchService | None = None,
        handoff_service: HandoffService | None = None,
        ai_client: OpenAIChatClient | None = None,
    ):
        self.lead_capture_service = lead_capture_service or LeadCaptureService()
        self.crm_dispatch_service = crm_dispatch_service or CRMDispatchService()
        self.handoff_service = handoff_service or HandoffService()
        self.ai_client = ai_client or OpenAIChatClient()

    def generate_reply(
        self,
        history: Iterable[dict[str, str]],
        current_message: str,
        conversation=None,
        assistant_profile=None,
        knowledge_context: str | None = None,
    ) -> LiviaReply:
        tenant = getattr(conversation, "tenant", None)
        profile_context = AssistantProfileContext.from_profile(assistant_profile, tenant=tenant)
        discovery = analyze_message(current_message)
        classification = discovery.to_dict()
        intent = discovery.intent
        if knowledge_context is None:
            knowledge_context = build_knowledge_context(
                tenant,
                current_message,
                service_area=discovery.service_area,
                limit=2,
                conversation=conversation,
            ) if tenant is not None else ""
        else:
            knowledge_context = str(knowledge_context or "")
        has_commercial_interest = bool(discovery.has_commercial_interest)
        has_quote_request = bool(discovery.has_quote_request)
        has_support_request = bool(discovery.has_support_request)
        has_technical_question = bool(discovery.has_technical_question)

        if intent == "greeting":
            decision = LiviaReply(intent=intent, reply=profile_context.initial_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if intent == "technical_question":
            if self._should_answer_informatively_from_knowledge(discovery, knowledge_context):
                reply = build_contextual_reply(intent="technical_question")
                decision = LiviaReply(intent=intent, reply=self._with_knowledge(reply, knowledge_context))
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            if discovery.should_ask_discovery_question and discovery.suggested_next_question:
                decision = self._ask_discovery_question(
                    discovery, conversation, knowledge_context=knowledge_context,
                    assistant_profile=assistant_profile, tenant=tenant,
                )
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            reply = build_contextual_reply(intent="technical_question")
            decision = LiviaReply(intent=intent, reply=self._with_knowledge(reply, knowledge_context))
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if intent == "support_request":
            decision = LiviaReply(
                intent=intent,
                reply=self._with_knowledge(build_contextual_reply(intent="support_request"), knowledge_context),
            )
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if intent in {"quote_request", "commercial_interest"}:
            if conversation is not None and should_lock_lead(conversation) and not can_start_new_cycle(conversation, current_message):
                decision = self._locked_lead_reply(intent)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            if self._should_ask_profile_discovery(current_message, assistant_profile):
                decision = self._ask_discovery_question(
                    discovery, conversation, knowledge_context=knowledge_context,
                    assistant_profile=assistant_profile, tenant=tenant, current_message=current_message,
                )
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            if should_combine_kb_with_discovery(discovery, conversation, knowledge_context):
                decision = self._ask_discovery_question(
                    discovery, conversation, knowledge_context=knowledge_context,
                    assistant_profile=assistant_profile, tenant=tenant, current_message=current_message,
                )
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            if self._should_answer_informatively_from_knowledge(discovery, knowledge_context):
                decision = LiviaReply(
                    intent=intent,
                    reply=self._with_knowledge(DEFAULT_REPLY, knowledge_context),
                )
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            if not discovery.should_collect_lead and discovery.should_ask_discovery_question:
                decision = self._ask_discovery_question(
                    discovery, conversation, knowledge_context=knowledge_context,
                    assistant_profile=assistant_profile, tenant=tenant,
                )
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            return self._handle_qualification(
                intent=intent,
                history=history,
                current_message=current_message,
                conversation=conversation,
                discovery=discovery,
                assistant_profile=assistant_profile,
                knowledge_context=knowledge_context,
            )
        if intent == "contact_data":
            if self._should_start_lead_from_contact(current_message, has_commercial_interest, has_quote_request):
                return self._handle_qualification(
                    intent="contact_data",
                    history=history,
                    current_message=current_message,
                    conversation=conversation,
                    discovery=discovery,
                    assistant_profile=assistant_profile,
                    knowledge_context=knowledge_context,
                )
            decision = LiviaReply(
                intent=intent,
                reply=build_contextual_reply(intent="contact_data"),
            )
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if has_basic_contact(current_message) and (has_commercial_interest or has_quote_request):
            if conversation is not None and should_lock_lead(conversation) and not can_start_new_cycle(conversation, current_message):
                decision = self._locked_lead_reply("contact_data")
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            if not discovery.should_collect_lead and discovery.should_ask_discovery_question:
                decision = self._ask_discovery_question(
                    discovery, conversation, knowledge_context=knowledge_context,
                    assistant_profile=assistant_profile, tenant=tenant,
                )
                decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
                return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
            return self._handle_qualification(
                intent="contact_data",
                history=history,
                current_message=current_message,
                conversation=conversation,
                discovery=discovery,
                assistant_profile=assistant_profile,
                knowledge_context=knowledge_context,
            )
        if has_support_request or has_technical_question:
            decision = LiviaReply(
                intent=intent,
                reply=self._with_knowledge(build_contextual_reply(intent=intent), knowledge_context),
            )
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if self._is_followup_from_history(history):
            decision = LiviaReply(
                intent="followup",
                reply=self._with_knowledge(
                    "Perfeito. Me conte um pouco mais sobre o contexto para eu te orientar.",
                    knowledge_context,
                ),
            )
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if discovery.should_ask_discovery_question and has_semantic_knowledge_block(knowledge_context) and not is_informational_knowledge_query(discovery):
            decision = self._ask_discovery_question(
                discovery,
                conversation,
                knowledge_context=knowledge_context,
                assistant_profile=assistant_profile,
                tenant=tenant,
            )
            decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        decision = LiviaReply(
            intent=intent,
            reply=self._with_knowledge(DEFAULT_REPLY, knowledge_context),
        )
        decision = self._finalize_handoff(decision, conversation, None, discovery, current_message)
        return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)


    def _finalize_ai_response(
        self,
        decision: LiviaReply,
        conversation,
        assistant_profile,
        discovery,
        current_message: str,
        history: Iterable[dict[str, str]],
        knowledge_context: str,
    ) -> LiviaReply:
        if not self._should_try_ai(assistant_profile):
            return decision
        tenant = getattr(conversation, "tenant", None)
        lead_state = str(getattr(conversation, "lead_state", "") or "")
        try:
            messages = build_livia_ai_prompt(
                tenant=tenant,
                assistant_profile=assistant_profile,
                message=current_message,
                conversation=conversation,
                discovery_result=discovery,
                lead_state=lead_state,
                knowledge_context=knowledge_context,
                deterministic_reply=decision.reply,
                history=list(history or []),
            )
            result = self.ai_client.create_chat_completion(messages=messages)
        except Exception as exc:  # pragma: no cover - defensive local guard
            logger.warning("livia_ai_finalize_failed error_type=%s", exc.__class__.__name__)
            return decision
        if result.success and result.text:
            return replace(decision, reply=result.text)
        return decision

    def _should_try_ai(self, assistant_profile) -> bool:
        return bool(getattr(settings, "LIVIA_AI_ENABLED", False)) and bool(getattr(assistant_profile, "use_ai", False))


    def _finalize_handoff(self, decision: LiviaReply, conversation, lead_draft, discovery, current_message: str) -> LiviaReply:
        result = self.handoff_service.create_or_update_handoff(
            conversation,
            lead_draft=lead_draft,
            discovery_result=discovery,
            message=current_message,
        )
        if result.handoff is None:
            return decision
        decision = replace(
            decision,
            handoff_request_id=result.handoff.pk,
            handoff_reason=result.handoff.reason,
        )
        if not result.created:
            return decision
        confirmation = self._handoff_confirmation(result.handoff)
        if confirmation.lower() in decision.reply.lower():
            return decision
        return replace(decision, reply=f"{confirmation}\n\n{decision.reply}")

    def _handoff_confirmation(self, handoff) -> str:
        has_contact = bool(handoff.visitor_phone or handoff.visitor_email)
        name = handoff.visitor_name or handoff.visitor_company
        if has_contact:
            if name:
                return f"Perfeito, {name}. Registrei o pedido de contato para a equipe retornar com o contexto da conversa."
            return "Perfeito. Registrei o pedido de contato para a equipe retornar com o contexto da conversa."
        return "Claro. Vou registrar seu pedido para atendimento humano. Para agilizar, me informe seu nome e um telefone ou e-mail de contato."

    def _locked_lead_reply(self, intent: str) -> LiviaReply:
        return LiviaReply(
            intent=intent,
            reply="Perfeito, já encaminhei seus dados para sequência do atendimento. Se for uma nova demanda, me diga que é um novo pedido.",
        )

    def _ask_discovery_question(self, discovery, conversation, knowledge_context: str = "", assistant_profile=None, tenant=None, current_message: str = "") -> LiviaReply:
        if conversation is not None:
            next_state = LeadState.COLLECT_NEED if discovery.intent in {"quote_request", "commercial_interest"} else LeadState.DISCOVERY
            set_state(conversation, next_state)
        profile_ctx = AssistantProfileContext.from_profile(assistant_profile, tenant=tenant or getattr(conversation, "tenant", None))
        if profile_ctx.business_domain:
            reply = resolve_discovery_question(
                discovery.service_area,
                business_domain=profile_ctx.business_domain,
                business_name=profile_ctx.business_name,
                short_description=profile_ctx.short_description,
                primary_goal=profile_ctx.primary_goal,
                current_message=current_message,
                knowledge_context=knowledge_context,
            )
        elif discovery.suggested_next_question:
            reply = discovery.suggested_next_question
        else:
            reply = resolve_discovery_question(
                discovery.service_area,
                business_domain=profile_ctx.business_domain,
                business_name=profile_ctx.business_name,
                short_description=profile_ctx.short_description,
                primary_goal=profile_ctx.primary_goal,
                current_message=current_message,
                knowledge_context=knowledge_context,
            )
        return LiviaReply(intent=discovery.intent, reply=self._with_knowledge(reply, knowledge_context))


    def _should_ask_profile_discovery(self, current_message: str, assistant_profile) -> bool:
        if assistant_profile is None:
            return False
        return should_ask_profile_discovery(
            current_message=current_message,
            business_domain=getattr(assistant_profile, "business_domain", ""),
            short_description=getattr(assistant_profile, "short_description", ""),
            primary_goal=getattr(assistant_profile, "primary_goal", ""),
        )

    def _with_knowledge(self, reply: str, knowledge_context: str) -> str:
        """
        Ecoa trechos curtos só do retriever textual (KnowledgeDocument).

        Contexto semântico RAG (bloco com Score:) fica exclusivo do prompt de IA —
        não deve ser prefixado na reply determinística ao visitante.
        """
        text = str(knowledge_context or "")
        if not text.strip():
            return reply
        # Marcador presente apenas em format_knowledge_base_block (retrieval semântico).
        if "\nScore:" in text or "\nscore:" in text.lower():
            return reply
        hints = self._knowledge_hints(text)
        if not hints:
            return reply
        return f"{hints}\n\n{reply}"

    def _knowledge_hints(self, knowledge_context: str) -> str:
        """Extrai trechos curtos e seguros para a resposta determinística ao visitante."""
        text = str(knowledge_context or "")
        if not text.strip():
            return ""

        contents: list[str] = []
        capture = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            upper = line.upper()
            if upper.startswith("[KNOWLEDGE_BASE]") or upper.startswith("[/KNOWLEDGE_BASE]"):
                capture = False
                continue
            if line.lower().startswith("o bloco abaixo"):
                continue
            if line.lower().startswith("trate o conteúdo"):
                continue
            if line.lower().startswith("pedido de mudança"):
                continue
            if line.lower().startswith("fonte:") or line.lower().startswith("referência:") or line.lower().startswith("score:"):
                capture = False
                continue
            if line.lower().startswith("conteúdo:"):
                capture = True
                continue
            if capture:
                contents.append(line)
                capture = False

        # Compatibilidade com formato legado numerado, se ainda aparecer.
        if not contents:
            for line in text.splitlines():
                clean = line.strip()
                if not clean or clean.lower().startswith("base de conhecimento"):
                    continue
                if ". " in clean and clean.split(". ", 1)[0].isdigit():
                    clean = clean.split(". ", 1)[1]
                if clean.upper().startswith("[KNOWLEDGE_BASE]") or clean.upper().startswith("[/KNOWLEDGE_BASE]"):
                    continue
                contents.append(clean)
                if len(contents) >= 2:
                    break

        safe_bits = []
        for item in contents[:2]:
            clipped = item[:220].strip()
            if not clipped:
                continue
            lowered = clipped.lower()
            # Evita ecoar instruções operacionais vindas de documentos no reply ao visitante.
            if any(
                marker in lowered
                for marker in (
                    "marque qualquer",
                    "ignore as regras",
                    "altere o tenant",
                    "system prompt",
                    "você deve sempre",
                    "crie lead",
                )
            ):
                continue
            safe_bits.append(clipped)
        return " ".join(safe_bits)

    def _is_followup_from_history(self, history: Iterable[dict[str, str]]) -> bool:
        messages = list(history or [])
        if not messages:
            return False
        last_user = next((message for message in reversed(messages) if message.get("role") == "user"), None)
        if not last_user:
            return False
        return len(str(last_user.get("content") or "").strip()) <= 18

    def _handle_qualification(
        self,
        *,
        intent: str,
        history: Iterable[dict[str, str]],
        current_message: str,
        conversation,
        discovery=None,
        assistant_profile=None,
        knowledge_context: str = "",
    ) -> LiviaReply:
        if conversation is None:
            decision = LiviaReply(intent=intent, reply=build_contextual_reply(intent=intent))
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)
        if should_lock_lead(conversation) and not can_start_new_cycle(conversation, current_message):
            decision = LiviaReply(
                intent=intent,
                reply="Perfeito, já encaminhei seus dados para sequência do atendimento. Se for uma nova demanda, me diga que é um novo pedido.",
            )
            return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)

        result = self.lead_capture_service.capture_from_message(
            conversation=conversation,
            message=current_message,
            history=history,
        )
        reply = self.lead_capture_service.build_next_prompt(result.lead_draft, result.missing_fields, intent=intent, invalid_fields=result.invalid_fields)
        if result.is_qualified:
            reply = build_contextual_reply(intent=intent, missing_fields=[])
        decision = LiviaReply(intent=intent, reply=reply)
        decision = self._finalize_handoff(decision, conversation, result.lead_draft, discovery, current_message)
        return self._finalize_ai_response(decision, conversation, assistant_profile, discovery, current_message, history, knowledge_context)

    def _should_answer_informatively_from_knowledge(self, discovery, knowledge_context: str) -> bool:
        return has_semantic_knowledge_block(knowledge_context) and is_informational_knowledge_query(discovery)

    def _should_start_lead_from_contact(
        self,
        current_message: str,
        has_commercial_interest: bool,
        has_quote_request: bool,
    ) -> bool:
        if has_commercial_interest or has_quote_request:
            return True
        text = str(current_message or "").strip().lower()
        if not text or not has_basic_contact(current_message):
            return False
        return True
