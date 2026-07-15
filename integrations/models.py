from django.core.exceptions import ValidationError
from django.db import models

from conversations.models import HandoffRequest
from leads.models import LeadDraft
from tenants.models import Tenant


class TenantWebhookConfig(models.Model):
    class EventType(models.TextChoices):
        HANDOFF_CREATED = "handoff_created", "Handoff created"
        LEAD_QUALIFIED = "lead_qualified", "Lead qualified"
        CONVERSATION_SUMMARY = "conversation_summary", "Conversation summary"
        ALL = "all", "All"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="webhook_configs")
    name = models.CharField(max_length=120)
    event_type = models.CharField(max_length=40, choices=EventType.choices, default=EventType.ALL)
    target_url = models.URLField(max_length=500)
    secret_token = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    dry_run = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant__name", "name"]

    def clean(self):
        super().clean()
        if self.target_url and not self.target_url.startswith(("http://", "https://")):
            raise ValidationError({"target_url": "Webhook target URL must use http or https."})

    def __str__(self):
        return f"{self.tenant.slug} / {self.name}"


class WebhookDeliveryLog(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DRY_RUN = "dry_run", "Dry run"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="webhook_delivery_logs")
    webhook_config = models.ForeignKey(
        TenantWebhookConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_logs",
    )
    event_type = models.CharField(max_length=40, choices=TenantWebhookConfig.EventType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    status_code = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    payload_preview = models.JSONField(default=dict, blank=True)
    related_handoff = models.ForeignKey(
        HandoffRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_delivery_logs",
    )
    related_lead = models.ForeignKey(
        LeadDraft,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_delivery_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tenant.slug} / {self.event_type} / {self.status}"
