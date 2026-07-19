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
