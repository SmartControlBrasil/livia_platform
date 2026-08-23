from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Count, Max, Q
from django.utils import timezone

from integrations.models import OutboxEvent, TenantWebhookConfig, WebhookDeliveryLog
from integrations.side_effect_policy import SideEffectDecision, SideEffectStatus, SideEffectType, evaluate_side_effect_policy
from knowledge_base.models import TenantRagConfiguration
from knowledge_base.rag.operations_readiness import inspect_rag_operations_readiness
from knowledge_base.rag.readiness import inspect_rag_vector_readiness
from tenants.services.install_package import TenantInstallPackageService


STATUS_TONES = {
    "BLOCKED": "danger",
    "DRY_RUN": "warning",
    "REAL_ENABLED": "success",
    "OK": "success",
    "WARNING": "warning",
    "NOT_READY": "danger",
}


def build_integrations_context(*, tenant):
    decisions = _build_side_effect_decisions(tenant=tenant)
    integration_rows = [_build_integration_row(tenant=tenant, decision=decision) for decision in decisions]
    outbox_queryset = OutboxEvent.objects.filter(tenant=tenant).select_related("tenant").order_by("-created_at")
    webhook_logs = WebhookDeliveryLog.objects.filter(tenant=tenant).select_related("webhook_config").order_by("-created_at")
    install_package = TenantInstallPackageService().build_for_tenant(tenant)
    readiness_rows = _build_readiness_rows(tenant=tenant, decisions=decisions, install_package=install_package)
    return {
        "integration_rows": integration_rows,
        "readiness_rows": readiness_rows,
        "outbox_events": list(outbox_queryset[:50]),
        "outbox_status_choices": OutboxEvent.Status.choices,
        "outbox_event_type_choices": OutboxEvent.EventType.choices,
        "outbox_summary": _build_outbox_summary(outbox_queryset),
        "webhook_configs": list(TenantWebhookConfig.objects.filter(tenant=tenant).order_by("name")),
        "webhook_logs": list(webhook_logs[:20]),
        "site_readiness": install_package.readiness,
        "site_readiness_status": install_package.readiness_status,
    }


def filter_outbox_queryset(*, tenant, status="", event_type=""):
    queryset = OutboxEvent.objects.filter(tenant=tenant).select_related("tenant").order_by("-created_at")
    if status:
        queryset = queryset.filter(status=status)
    if event_type:
        queryset = queryset.filter(event_type=event_type)
    return queryset


def requeue_outbox_event_for_portal(*, event):
    if event.status not in {OutboxEvent.Status.DEAD_LETTER, OutboxEvent.Status.RETRY}:
        return None
    before = {"status": event.status, "attempts": event.attempts, "locked_by": event.locked_by}
    event.status = OutboxEvent.Status.PENDING
    event.available_at = timezone.now()
    event.locked_at = None
    event.locked_by = ""
    event.last_error_code = "manual_requeue_from_portal"
    event.last_error_message = "Manual requeue from operations portal."
    event.save(update_fields=["status", "available_at", "locked_at", "locked_by", "last_error_code", "last_error_message", "updated_at"])
    return before, {"status": event.status, "attempts": event.attempts, "locked_by": event.locked_by}


def _build_side_effect_decisions(*, tenant):
    rag_cfg = TenantRagConfiguration.objects.filter(tenant=tenant).first()
    decisions = [
        evaluate_side_effect_policy(
            side_effect=SideEffectType.OPENAI_CHAT,
            tenant=tenant,
            integration_configured=bool(str(getattr(settings, "LIVIA_OPENAI_API_KEY", "") or "").strip()),
        ),
        evaluate_side_effect_policy(
            side_effect=SideEffectType.OPENAI_EMBEDDING,
            tenant=tenant,
            integration_configured=bool(str(getattr(settings, "LIVIA_RAG_EMBEDDING_API_KEY", "") or "").strip()),
        ),
        _google_drive_decision_for_tenant(tenant=tenant, rag_cfg=rag_cfg),
        evaluate_side_effect_policy(
            side_effect=SideEffectType.SMART360_LEAD_DISPATCH,
            tenant=tenant,
            integration_configured=bool(
                str(getattr(settings, "SMART360_BASE_URL", "") or "").strip()
                and str(getattr(settings, "SMART360_M2M_TOKEN", "") or "").strip()
            ),
        ),
        evaluate_side_effect_policy(side_effect=SideEffectType.WEBHOOK_DELIVERY, tenant=tenant, integration_configured=True),
        evaluate_side_effect_policy(side_effect=SideEffectType.EMAIL_NOTIFICATION, tenant=tenant, integration_configured=True),
        evaluate_side_effect_policy(side_effect=SideEffectType.WHATSAPP_HANDOFF, tenant=tenant, integration_configured=True),
    ]
    if rag_cfg is None or not rag_cfg.retrieval_enabled:
        decisions[1] = SideEffectDecision(
            side_effect=SideEffectType.OPENAI_EMBEDDING,
            status=SideEffectStatus.BLOCKED,
            allowed=False,
            dry_run=False,
            code="tenant_retrieval_disabled",
            reason="Retrieval semântico do tenant está desabilitado.",
        )
    return decisions


def _google_drive_decision_for_tenant(*, tenant, rag_cfg):
    if rag_cfg is None or not getattr(rag_cfg, "sync_enabled", False):
        return SideEffectDecision(
            side_effect=SideEffectType.GOOGLE_DRIVE_SYNC,
            status=SideEffectStatus.BLOCKED,
            allowed=False,
            dry_run=False,
            code="drive_sync_not_required",
            reason="Google Drive sync não é requerido para este tenant.",
        )
    if getattr(rag_cfg, "source_mode", "") != TenantRagConfiguration.SOURCE_GOOGLE_DRIVE:
        return SideEffectDecision(
            side_effect=SideEffectType.GOOGLE_DRIVE_SYNC,
            status=SideEffectStatus.BLOCKED,
            allowed=False,
            dry_run=False,
            code="drive_sync_configuration_inconsistent",
            reason="Sync Google Drive habilitado sem source_mode=google_drive.",
        )
    if not str(getattr(rag_cfg, "approved_folder_id", "") or "").strip():
        return SideEffectDecision(
            side_effect=SideEffectType.GOOGLE_DRIVE_SYNC,
            status=SideEffectStatus.BLOCKED,
            allowed=False,
            dry_run=False,
            code="drive_sync_missing_approved_folder",
            reason="Google Drive sync sem pasta aprovada.",
        )
    if not bool(getattr(settings, "LIVIA_RAG_OPERATIONS_ENABLED", False)) and not bool(getattr(settings, "LIVIA_RAG_INDEXING_ENABLED", False)):
        return SideEffectDecision(
            side_effect=SideEffectType.GOOGLE_DRIVE_SYNC,
            status=SideEffectStatus.BLOCKED,
            allowed=False,
            dry_run=False,
            code="rag_ops_and_indexing_disabled",
            reason="Operações/indexação RAG estão desabilitadas globalmente.",
        )
    return evaluate_side_effect_policy(
        side_effect=SideEffectType.GOOGLE_DRIVE_SYNC,
        tenant=tenant,
        integration_configured=bool(str(getattr(settings, "LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE", "") or "").strip()),
    )


def _build_integration_row(*, tenant, decision):
    event_type_filter = _event_types_for_side_effect(decision.side_effect)
    outbox = OutboxEvent.objects.filter(tenant=tenant)
    if event_type_filter:
        outbox = outbox.filter(event_type__in=event_type_filter)
    latest_event = outbox.order_by("-last_attempt_at", "-created_at").first()
    latest_error = outbox.exclude(last_error_message="").order_by("-updated_at").first()
    latest_success = outbox.filter(status=OutboxEvent.Status.SUCCEEDED).order_by("-processed_at", "-updated_at").first()
    webhook_detail = ""
    if decision.side_effect == SideEffectType.WEBHOOK_DELIVERY:
        total = TenantWebhookConfig.objects.filter(tenant=tenant).count()
        active = TenantWebhookConfig.objects.filter(tenant=tenant, is_active=True).count()
        dry = TenantWebhookConfig.objects.filter(tenant=tenant, is_active=True, dry_run=True).count()
        webhook_detail = f"webhook_configs={total} active={active} dry_run={dry}"
    return {
        "name": decision.side_effect.value,
        "status": decision.status.value,
        "tone": STATUS_TONES.get(decision.status.value, "secondary"),
        "dry_run": decision.dry_run,
        "allowed": decision.allowed,
        "code": decision.code,
        "reason": decision.reason,
        "readiness": _decision_readiness(decision),
        "readiness_tone": STATUS_TONES.get(_decision_readiness(decision), "secondary"),
        "pending": outbox.filter(status=OutboxEvent.Status.PENDING).count(),
        "failed": outbox.filter(status=OutboxEvent.Status.DEAD_LETTER).count(),
        "retry": outbox.filter(status=OutboxEvent.Status.RETRY).count(),
        "latest_attempt_at": getattr(latest_event, "last_attempt_at", None),
        "latest_success_at": getattr(latest_success, "processed_at", None),
        "latest_error": getattr(latest_error, "last_error_message", ""),
        "detail": webhook_detail,
    }


def _build_outbox_summary(queryset):
    counts = {status: 0 for status, _label in OutboxEvent.Status.choices}
    for row in queryset.values("status").annotate(total=Count("id")):
        counts[row["status"]] = row["total"]
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "pending": counts.get(OutboxEvent.Status.PENDING, 0),
        "failed": counts.get(OutboxEvent.Status.DEAD_LETTER, 0),
        "retry": counts.get(OutboxEvent.Status.RETRY, 0),
        "latest_created_at": queryset.aggregate(value=Max("created_at"))["value"],
    }


def _build_readiness_rows(*, tenant, decisions, install_package):
    rows = [
        {
            "group": "Instalação",
            "code": "site_readiness",
            "status": install_package.readiness_status,
            "tone": STATUS_TONES.get(install_package.readiness_status, "secondary"),
            "detail": "tenant_site_readiness",
        }
    ]
    for check in install_package.readiness.checks:
        rows.append({
            "group": "Instalação",
            "code": check.code,
            "status": check.status,
            "tone": "success" if check.status == "PASS" else "warning" if check.status == "WARN" else "danger",
            "detail": check.message,
        })
    for decision in decisions:
        rows.append({
            "group": "Side effects",
            "code": decision.side_effect.value,
            "status": decision.status.value,
            "tone": STATUS_TONES.get(decision.status.value, "secondary"),
            "detail": f"{decision.code}: {decision.reason}",
        })
    for check in inspect_rag_operations_readiness(tenant=tenant):
        rows.append({
            "group": "RAG operations",
            "code": check.code,
            "status": "OK" if check.ok else "WARNING" if check.severity == "warning" else "BLOCKED",
            "tone": "success" if check.ok else "warning" if check.severity == "warning" else "danger",
            "detail": check.detail,
        })
    for check in inspect_rag_vector_readiness():
        rows.append({
            "group": "RAG vector",
            "code": check.code,
            "status": "OK" if check.ok else "BLOCKED",
            "tone": "success" if check.ok else "danger",
            "detail": check.detail,
        })
    summary = _build_outbox_summary(OutboxEvent.objects.filter(tenant=tenant))
    rows.append({
        "group": "Outbox",
        "code": "pending_failed_retry",
        "status": "OK" if summary["failed"] == 0 else "WARNING",
        "tone": "success" if summary["failed"] == 0 else "warning",
        "detail": f"pending={summary['pending']} retry={summary['retry']} dead_letter={summary['failed']}",
    })
    return rows


def _event_types_for_side_effect(side_effect):
    if side_effect == SideEffectType.SMART360_LEAD_DISPATCH:
        return [OutboxEvent.EventType.LEAD_QUALIFIED]
    if side_effect == SideEffectType.WEBHOOK_DELIVERY:
        return [OutboxEvent.EventType.LEAD_QUALIFIED, OutboxEvent.EventType.HANDOFF_CREATED, OutboxEvent.EventType.CONVERSATION_SUMMARY_READY]
    if side_effect in {SideEffectType.EMAIL_NOTIFICATION, SideEffectType.WHATSAPP_HANDOFF}:
        return [OutboxEvent.EventType.HANDOFF_CREATED]
    return []


def _decision_readiness(decision):
    if decision.status == SideEffectStatus.REAL_ENABLED:
        return "READY"
    if decision.status == SideEffectStatus.DRY_RUN:
        return "WARNING"
    if decision.code in {
        "openai_chat_disabled",
        "tenant_retrieval_disabled",
        "drive_sync_not_required",
        "smart360_dry_run",
        "webhooks_disabled",
        "email_notifications_disabled",
        "whatsapp_handoff_client_side_only",
    }:
        return "READY"
    return "BLOCKED"
