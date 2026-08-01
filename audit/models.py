from django.conf import settings
from django.db import models


ACTION_HANDOFF_STATUS_CHANGED = "handoff.status_changed"
ACTION_LEAD_CRM_DISPATCH_RETRIED = "lead.crm_dispatch_retried"
ACTION_ASSISTANT_PROFILE_UPDATED = "assistant_profile.updated"
ACTION_TENANT_CREATED = "tenant.created"
ACTION_TENANT_UPDATED = "tenant.updated"
ACTION_KNOWLEDGE_DOCUMENT_CREATED = "knowledge_document.created"
ACTION_KNOWLEDGE_DOCUMENT_UPDATED = "knowledge_document.updated"
ACTION_WEBHOOK_CONFIG_CREATED = "webhook_config.created"
ACTION_WEBHOOK_CONFIG_UPDATED = "webhook_config.updated"
ACTION_TENANT_MEMBERSHIP_CREATED = "tenant_membership.created"
ACTION_TENANT_MEMBERSHIP_UPDATED = "tenant_membership.updated"
ACTION_TENANT_MEMBERSHIP_DEACTIVATED = "tenant_membership.deactivated"
ACTION_TENANT_ORIGIN_CREATED = "tenant_origin.created"
ACTION_TENANT_ORIGIN_UPDATED = "tenant_origin.updated"
ACTION_TENANT_ORIGIN_DEACTIVATED = "tenant_origin.deactivated"
ACTION_OUTBOX_REQUEUED = "outbox.requeued"
ACTION_OUTBOX_DEAD_LETTERED = "outbox.dead_lettered"
ACTION_TENANT_RAG_CONFIGURED = "tenant.rag_configured"
ACTION_TENANT_RAG_INDEX_STARTED = "tenant.rag_index_started"
ACTION_TENANT_RAG_INDEX_COMPLETED = "tenant.rag_index_completed"
ACTION_TENANT_RAG_INDEX_FAILED = "tenant.rag_index_failed"
ACTION_TENANT_RAG_DIAGNOSTIC_SEARCH = "tenant.rag_diagnostic_search"
ACTION_TENANT_RAG_OPERATION_REQUESTED = "tenant.rag_operation_requested"
ACTION_TENANT_RAG_OPERATION_REJECTED = "tenant.rag_operation_rejected"
ACTION_TENANT_RAG_OPERATION_STARTED = "tenant.rag_operation_started"
ACTION_TENANT_RAG_OPERATION_COMPLETED = "tenant.rag_operation_completed"
ACTION_TENANT_RAG_OPERATION_FAILED = "tenant.rag_operation_failed"
ACTION_TENANT_RAG_OPERATION_DUPLICATE = "tenant.rag_operation_duplicate"
ACTION_TENANT_RAG_OPERATION_STALE_RECOVERED = "tenant.rag_operation_stale_recovered"


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        HANDOFF_STATUS_CHANGED = ACTION_HANDOFF_STATUS_CHANGED, "Handoff status changed"
        LEAD_CRM_DISPATCH_RETRIED = ACTION_LEAD_CRM_DISPATCH_RETRIED, "Lead CRM dispatch retried"
        ASSISTANT_PROFILE_UPDATED = ACTION_ASSISTANT_PROFILE_UPDATED, "Assistant profile updated"
        TENANT_CREATED = ACTION_TENANT_CREATED, "Tenant created"
        TENANT_UPDATED = ACTION_TENANT_UPDATED, "Tenant updated"
        KNOWLEDGE_DOCUMENT_CREATED = ACTION_KNOWLEDGE_DOCUMENT_CREATED, "Knowledge document created"
        KNOWLEDGE_DOCUMENT_UPDATED = ACTION_KNOWLEDGE_DOCUMENT_UPDATED, "Knowledge document updated"
        WEBHOOK_CONFIG_CREATED = ACTION_WEBHOOK_CONFIG_CREATED, "Webhook config created"
        WEBHOOK_CONFIG_UPDATED = ACTION_WEBHOOK_CONFIG_UPDATED, "Webhook config updated"
        TENANT_MEMBERSHIP_CREATED = ACTION_TENANT_MEMBERSHIP_CREATED, "Tenant membership created"
        TENANT_MEMBERSHIP_UPDATED = ACTION_TENANT_MEMBERSHIP_UPDATED, "Tenant membership updated"
        TENANT_MEMBERSHIP_DEACTIVATED = ACTION_TENANT_MEMBERSHIP_DEACTIVATED, "Tenant membership deactivated"
        TENANT_ORIGIN_CREATED = ACTION_TENANT_ORIGIN_CREATED, "Tenant origin created"
        TENANT_ORIGIN_UPDATED = ACTION_TENANT_ORIGIN_UPDATED, "Tenant origin updated"
        TENANT_ORIGIN_DEACTIVATED = ACTION_TENANT_ORIGIN_DEACTIVATED, "Tenant origin deactivated"
        OUTBOX_REQUEUED = ACTION_OUTBOX_REQUEUED, "Outbox requeued"
        OUTBOX_DEAD_LETTERED = ACTION_OUTBOX_DEAD_LETTERED, "Outbox dead lettered"
        TENANT_RAG_CONFIGURED = ACTION_TENANT_RAG_CONFIGURED, "Tenant RAG configured"
        TENANT_RAG_INDEX_STARTED = ACTION_TENANT_RAG_INDEX_STARTED, "Tenant RAG index started"
        TENANT_RAG_INDEX_COMPLETED = ACTION_TENANT_RAG_INDEX_COMPLETED, "Tenant RAG index completed"
        TENANT_RAG_INDEX_FAILED = ACTION_TENANT_RAG_INDEX_FAILED, "Tenant RAG index failed"
        TENANT_RAG_DIAGNOSTIC_SEARCH = ACTION_TENANT_RAG_DIAGNOSTIC_SEARCH, "Tenant RAG diagnostic search"
        TENANT_RAG_OPERATION_REQUESTED = ACTION_TENANT_RAG_OPERATION_REQUESTED, "Tenant RAG operation requested"
        TENANT_RAG_OPERATION_REJECTED = ACTION_TENANT_RAG_OPERATION_REJECTED, "Tenant RAG operation rejected"
        TENANT_RAG_OPERATION_STARTED = ACTION_TENANT_RAG_OPERATION_STARTED, "Tenant RAG operation started"
        TENANT_RAG_OPERATION_COMPLETED = ACTION_TENANT_RAG_OPERATION_COMPLETED, "Tenant RAG operation completed"
        TENANT_RAG_OPERATION_FAILED = ACTION_TENANT_RAG_OPERATION_FAILED, "Tenant RAG operation failed"
        TENANT_RAG_OPERATION_DUPLICATE = ACTION_TENANT_RAG_OPERATION_DUPLICATE, "Tenant RAG operation duplicate"
        TENANT_RAG_OPERATION_STALE_RECOVERED = (
            ACTION_TENANT_RAG_OPERATION_STALE_RECOVERED,
            "Tenant RAG operation stale recovered",
        )

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=80, choices=Action.choices)
    object_type = models.CharField(max_length=120)
    object_id = models.CharField(max_length=120, blank=True)
    object_repr = models.CharField(max_length=220, blank=True)
    before_data = models.JSONField(default=dict, blank=True)
    after_data = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["object_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.action} / {self.object_type}:{self.object_id}"
