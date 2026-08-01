from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings

from assistant_core.discovery import analyze_message
from assistant_core.eval.faithfulness import (
    FAITHFULNESS_NO_KNOWLEDGE_REQUIRED,
    FAITHFULNESS_PARTIALLY_SUPPORTED,
    FAITHFULNESS_SUPPORTED,
    FAITHFULNESS_UNSUPPORTED,
    classify_faithfulness,
    contains_wrong_vertical,
)
from assistant_core.services.decision_outcome import resolve_decision_outcome
from assistant_core.services.grounded_response import GroundedResponseService
from assistant_core.services.livia_decision import LiviaDecisionService, LiviaReply
from knowledge_base.rag.context_builder import build_knowledge_context
from knowledge_base.rag.conversation_retrieval import retrieve_context
from tenants.models import AssistantProfile, Tenant


@dataclass
class ResponseEvalCase:
    case_id: str
    query: str
    expect: str
    category: str = ""
    facts_expected: list[str] = field(default_factory=list)
    facts_forbidden: list[str] = field(default_factory=list)
    require_knowledge: bool = True
    security_expectation: str = ""


@dataclass
class ResponseEvalResult:
    case_id: str
    category: str
    expect: str
    retrieval_status: str
    retrieval_hit: bool
    ai_used: bool
    ai_status: str
    reply: str
    faithfulness: str
    faithfulness_notes: str = ""
    wrong_vertical: bool = False
    latency_ms: int = 0


def load_response_eval_cases(path: Path) -> list[ResponseEvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases: list[ResponseEvalCase] = []
    for item in raw:
        if not item.get("response_eval"):
            continue
        cases.append(
            ResponseEvalCase(
                case_id=str(item["id"]),
                query=str(item["query"]),
                expect=str(item.get("expect", "")),
                category=str(item.get("category", "")),
                facts_expected=[str(x) for x in item.get("facts_expected", [])],
                facts_forbidden=[str(x) for x in item.get("facts_forbidden", [])],
                require_knowledge=bool(item.get("require_knowledge", item.get("expect") == "hit")),
                security_expectation=str(item.get("security_expectation", "") or ""),
            )
        )
    return cases


class ResponseFaithfulnessRunner:
    def __init__(self, *, tenant: Tenant, assistant_profile: AssistantProfile, ai_client=None):
        self.tenant = tenant
        self.profile = assistant_profile
        self.decision_service = LiviaDecisionService(ai_client=ai_client)
        self.grounded_service = GroundedResponseService(ai_client=ai_client or self.decision_service.ai_client)

    def run_case(self, case: ResponseEvalCase) -> ResponseEvalResult:
        started = time.monotonic()
        discovery = analyze_message(case.query)
        knowledge_context = build_knowledge_context(
            self.tenant,
            case.query,
            service_area=discovery.service_area,
            limit=3,
            conversation=None,
        )
        retrieval = retrieve_context(
            tenant=self.tenant,
            query=case.query,
            conversation=None,
            limit=3,
        )
        decision = self.decision_service.generate_reply(
            [],
            case.query,
            conversation=None,
            assistant_profile=SimpleNamespace(
                name=self.profile.name,
                initial_message=self.profile.initial_message,
                tone=self.profile.tone,
                primary_goal=self.profile.primary_goal,
                business_name=self.profile.business_name or self.tenant.name,
                business_domain=self.profile.business_domain,
                short_description=self.profile.short_description,
                use_ai=False,
            ),
            knowledge_context=knowledge_context,
        )
        ai_used = False
        ai_status = "skipped"
        reply = decision.reply

        outcome = resolve_decision_outcome(
            decision=decision,
            discovery=discovery,
            conversation=None,
            knowledge_context=knowledge_context,
        )
        allow_grounded = bool(getattr(settings, "LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED", False)) and bool(
            self.profile.grounded_synthesis_enabled
        )
        if allow_grounded and outcome.allow_knowledge_synthesis:
            grounded = self.grounded_service.generate(
                tenant=self.tenant,
                assistant_profile=self.profile,
                message=case.query,
                conversation=None,
                discovery=discovery,
                decision=decision,
                knowledge_context=knowledge_context,
                history=[],
            )
            ai_status = grounded.status
            if grounded.used and grounded.text:
                ai_used = True
                reply = grounded.text

        faith = classify_faithfulness(
            reply,
            facts_expected=case.facts_expected,
            facts_forbidden=case.facts_forbidden,
            require_knowledge=case.require_knowledge,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        return ResponseEvalResult(
            case_id=case.case_id,
            category=case.category,
            expect=case.expect,
            retrieval_status=retrieval.status,
            retrieval_hit=bool(retrieval.chunks),
            ai_used=ai_used,
            ai_status=ai_status,
            reply=reply[:500],
            faithfulness=faith.status,
            faithfulness_notes=faith.notes,
            wrong_vertical=contains_wrong_vertical(reply),
            latency_ms=latency_ms,
        )

    def summarize(self, results: list[ResponseEvalResult]) -> dict[str, object]:
        counts = {
            FAITHFULNESS_SUPPORTED: 0,
            FAITHFULNESS_PARTIALLY_SUPPORTED: 0,
            FAITHFULNESS_UNSUPPORTED: 0,
            FAITHFULNESS_NO_KNOWLEDGE_REQUIRED: 0,
        }
        for row in results:
            counts[row.faithfulness] = counts.get(row.faithfulness, 0) + 1
        return {
            "total": len(results),
            "faithfulness": counts,
            "ai_used": sum(1 for r in results if r.ai_used),
            "wrong_vertical": sum(1 for r in results if r.wrong_vertical),
        }
