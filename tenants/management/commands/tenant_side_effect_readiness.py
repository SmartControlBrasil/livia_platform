from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.side_effect_policy import (
    SideEffectDecision,
    SideEffectStatus,
    SideEffectType,
    evaluate_side_effect_policy,
)
from knowledge_base.models import TenantRagConfiguration
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Audita gates de side effects externos por tenant sem executar integrações."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"] or "").strip()
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError(f"Tenant não encontrado: {tenant_slug}")

        decisions = self._build_decisions(tenant=tenant)
        overall_safe = all(item.status != SideEffectStatus.REAL_ENABLED for item in decisions)
        overall = "SAFE" if overall_safe else "UNSAFE"

        if options["json"]:
            payload = {
                "tenant": tenant.slug,
                "overall": overall,
                "environment": str(getattr(settings, "LIVIA_ENVIRONMENT", "development") or "development"),
                "decisions": [item.to_dict() for item in decisions],
            }
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(f"Tenant: {tenant.slug}")
            self.stdout.write("")
            for item in decisions:
                self.stdout.write(f"{item.side_effect.value:<24} {item.status.value}")
            self.stdout.write("")
            self.stdout.write(f"OVERALL: {overall}")

        if not overall_safe:
            raise CommandError("Integrações externas com execução real habilitada (UNSAFE).")

    def _build_decisions(self, *, tenant: Tenant) -> list[SideEffectDecision]:
        rag_cfg = TenantRagConfiguration.objects.filter(tenant=tenant).first()
        decisions: list[SideEffectDecision] = []

        decisions.append(
            evaluate_side_effect_policy(
                side_effect=SideEffectType.OPENAI_CHAT,
                tenant=tenant,
                integration_configured=bool(str(getattr(settings, "LIVIA_OPENAI_API_KEY", "") or "").strip()),
            )
        )

        embedding_decision = evaluate_side_effect_policy(
            side_effect=SideEffectType.OPENAI_EMBEDDING,
            tenant=tenant,
            integration_configured=bool(str(getattr(settings, "LIVIA_RAG_EMBEDDING_API_KEY", "") or "").strip()),
        )
        if rag_cfg is None or not rag_cfg.retrieval_enabled:
            embedding_decision = SideEffectDecision(
                side_effect=SideEffectType.OPENAI_EMBEDDING,
                status=SideEffectStatus.BLOCKED,
                allowed=False,
                dry_run=False,
                code="tenant_retrieval_disabled",
                reason="Retrieval semântico do tenant está desabilitado.",
            )
        decisions.append(embedding_decision)

        drive_decision = evaluate_side_effect_policy(
            side_effect=SideEffectType.GOOGLE_DRIVE_SYNC,
            tenant=tenant,
            integration_configured=bool(str(getattr(settings, "LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE", "") or "").strip()),
        )
        if not bool(getattr(settings, "LIVIA_RAG_OPERATIONS_ENABLED", False)) and not bool(
            getattr(settings, "LIVIA_RAG_INDEXING_ENABLED", False)
        ):
            drive_decision = SideEffectDecision(
                side_effect=SideEffectType.GOOGLE_DRIVE_SYNC,
                status=SideEffectStatus.BLOCKED,
                allowed=False,
                dry_run=False,
                code="rag_ops_and_indexing_disabled",
                reason="Operações/indexação RAG estão desabilitadas globalmente.",
            )
        decisions.append(drive_decision)

        decisions.append(
            evaluate_side_effect_policy(
                side_effect=SideEffectType.SMART360_LEAD_DISPATCH,
                tenant=tenant,
                integration_configured=bool(
                    str(getattr(settings, "SMART360_BASE_URL", "") or "").strip()
                    and str(getattr(settings, "SMART360_M2M_TOKEN", "") or "").strip()
                ),
            )
        )
        decisions.append(
            evaluate_side_effect_policy(
                side_effect=SideEffectType.WEBHOOK_DELIVERY,
                tenant=tenant,
                integration_configured=True,
            )
        )
        decisions.append(
            evaluate_side_effect_policy(
                side_effect=SideEffectType.EMAIL_NOTIFICATION,
                tenant=tenant,
                integration_configured=True,
            )
        )
        decisions.append(
            evaluate_side_effect_policy(
                side_effect=SideEffectType.WHATSAPP_HANDOFF,
                tenant=tenant,
                integration_configured=True,
            )
        )
        return decisions
