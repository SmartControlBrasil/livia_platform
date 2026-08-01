from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from django.conf import settings

from assistant_core.eval.evidence_sufficiency import (
    EvidenceSufficiency,
    assess_evidence_sufficiency,
    effective_synthesis_mode,
    parse_chunk_ids_from_context,
)
from assistant_core.prompts.grounded_ai import build_grounded_ai_prompt
from assistant_core.services.ai_feature_gates import is_grounded_synthesis_allowed
from assistant_core.services.decision_outcome import DecisionOutcome, resolve_decision_outcome
from assistant_core.services.ai_telemetry import record_ai_usage
from integrations.openai.client import OpenAIChatClient, OpenAIChatResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroundedResponseResult:
    text: str = ""
    used: bool = False
    status: str = "skipped"
    skip_reason: str = ""
    latency_ms: int = 0
    error_type: str = ""
    evidence_sufficiency: str = ""
    evidence_reason: str = ""


class GroundedResponseService:
    def __init__(self, ai_client: OpenAIChatClient | None = None):
        self.ai_client = ai_client or OpenAIChatClient()

    def should_synthesize(
        self,
        *,
        assistant_profile,
        knowledge_context: str,
        decision,
        conversation,
        discovery,
    ) -> tuple[bool, str]:
        if not bool(getattr(settings, "LIVIA_AI_ENABLED", False)):
            return False, "ai_disabled"
        tenant_slug = str(getattr(getattr(conversation, "tenant", None), "slug", "") or "")
        if not is_grounded_synthesis_allowed(tenant_slug=tenant_slug, assistant_profile=assistant_profile):
            return False, "grounded_disabled"

        outcome = resolve_decision_outcome(
            decision=decision,
            discovery=discovery,
            conversation=conversation,
            knowledge_context=knowledge_context,
        )
        if not outcome.allow_knowledge_synthesis:
            return False, outcome.skip_reason or outcome.kind
        return True, outcome.synthesis_mode

    def generate(
        self,
        *,
        tenant,
        assistant_profile,
        message: str,
        conversation,
        discovery,
        decision,
        knowledge_context: str,
        history: list[dict[str, str]] | None = None,
    ) -> GroundedResponseResult:
        allowed, reason = self.should_synthesize(
            assistant_profile=assistant_profile,
            knowledge_context=knowledge_context,
            decision=decision,
            conversation=conversation,
            discovery=discovery,
        )
        if not allowed:
            logger.info(
                "ai.grounded.skipped tenant_slug=%s reason=%s",
                getattr(tenant, "slug", ""),
                reason,
            )
            return GroundedResponseResult(status="skipped", skip_reason=reason)

        outcome = resolve_decision_outcome(
            decision=decision,
            discovery=discovery,
            conversation=conversation,
            knowledge_context=knowledge_context,
        )
        assessment = assess_evidence_sufficiency(
            message=message,
            knowledge_context=knowledge_context,
            chunk_ids=parse_chunk_ids_from_context(knowledge_context),
        )
        if assessment.status == EvidenceSufficiency.INSUFFICIENT:
            logger.info(
                "rag.evidence_insufficient tenant_slug=%s reason=%s category=%s retrieval_score=%.4f chunk_ids=%s",
                getattr(tenant, "slug", ""),
                assessment.reason,
                assessment.category,
                assessment.retrieval_score,
                ",".join(str(item) for item in assessment.chunk_ids) or "-",
            )
            return GroundedResponseResult(
                status="skipped",
                skip_reason="insufficient_evidence",
                evidence_sufficiency=assessment.status.value,
                evidence_reason=assessment.reason,
            )

        effective_mode = effective_synthesis_mode(base_mode=outcome.synthesis_mode, assessment=assessment)
        outcome = DecisionOutcome(
            kind=outcome.kind,
            allow_knowledge_synthesis=outcome.allow_knowledge_synthesis,
            skip_reason=outcome.skip_reason,
            synthesis_mode=effective_mode,
            evidence_sufficiency=assessment.status.value,
            evidence_reason=assessment.reason,
        )
        if assessment.status == EvidenceSufficiency.PARTIAL:
            logger.info(
                "rag.evidence_partial tenant_slug=%s reason=%s category=%s retrieval_score=%.4f chunk_ids=%s mode=%s",
                getattr(tenant, "slug", ""),
                assessment.reason,
                assessment.category,
                assessment.retrieval_score,
                ",".join(str(item) for item in assessment.chunk_ids) or "-",
                effective_mode,
            )

        lead_state = str(getattr(conversation, "lead_state", "") or "")
        started = time.monotonic()
        logger.info(
            "ai.grounded.started tenant_slug=%s outcome=%s mode=%s evidence=%s",
            getattr(tenant, "slug", ""),
            outcome.kind,
            outcome.synthesis_mode,
            outcome.evidence_sufficiency,
        )
        try:
            messages = build_grounded_ai_prompt(
                tenant=tenant,
                assistant_profile=assistant_profile,
                message=message,
                conversation=conversation,
                discovery_result=discovery,
                lead_state=lead_state,
                knowledge_context=knowledge_context,
                decision_outcome=outcome,
                deterministic_reply=decision.reply,
                history=list(history or []),
            )
            result: OpenAIChatResult = self.ai_client.create_chat_completion(messages=messages)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "ai.grounded.failed tenant_slug=%s error_type=%s latency_ms=%s",
                getattr(tenant, "slug", ""),
                exc.__class__.__name__,
                latency_ms,
            )
            record_ai_usage(
                tenant=tenant,
                operation="grounded_synthesis",
                model=str(getattr(settings, "LIVIA_OPENAI_MODEL", "") or ""),
                success=False,
                latency_ms=latency_ms,
                error_type=exc.__class__.__name__,
                metadata={"source": "grounded_response"},
            )
            return GroundedResponseResult(
                status="failed",
                skip_reason="provider_exception",
                latency_ms=latency_ms,
                error_type=exc.__class__.__name__,
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        if not result.success or not result.text:
            logger.warning(
                "ai.grounded.failed tenant_slug=%s error_type=%s latency_ms=%s",
                getattr(tenant, "slug", ""),
                result.error_type or "empty_response",
                latency_ms,
            )
            record_ai_usage(
                tenant=tenant,
                operation="grounded_synthesis",
                model=result.model or str(getattr(settings, "LIVIA_OPENAI_MODEL", "") or ""),
                success=False,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                latency_ms=latency_ms,
                error_type=result.error_type or "empty_response",
                metadata={"source": "grounded_response"},
            )
            return GroundedResponseResult(
                status="failed",
                skip_reason=result.error_type or "empty_response",
                latency_ms=latency_ms,
                error_type=result.error_type,
            )

        logger.info(
            "ai.grounded.completed tenant_slug=%s latency_ms=%s chars=%s tokens=%s",
            getattr(tenant, "slug", ""),
            latency_ms,
            len(result.text),
            result.total_tokens,
        )
        record_ai_usage(
            tenant=tenant,
            operation="grounded_synthesis",
            model=result.model or str(getattr(settings, "LIVIA_OPENAI_MODEL", "") or ""),
            success=True,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            latency_ms=latency_ms,
            metadata={"source": "grounded_response", "evidence": outcome.evidence_sufficiency},
        )
        return GroundedResponseResult(
            text=result.text.strip(),
            used=True,
            status="completed",
            latency_ms=latency_ms,
            evidence_sufficiency=outcome.evidence_sufficiency,
            evidence_reason=outcome.evidence_reason,
        )
