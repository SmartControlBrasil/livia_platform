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

class OutboxEvent(models.Model):
    class EventType(models.TextChoices):
        LEAD_QUALIFIED = "lead.qualified", "Lead qualified"
        HANDOFF_CREATED = "handoff.created", "Handoff created"
        CONVERSATION_SUMMARY_READY = "conversation.summary_ready", "Conversation summary ready"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        RETRY = "retry", "Retry"
        DEAD_LETTER = "dead_letter", "Dead letter"
        SKIPPED = "skipped", "Skipped"

    event_id = models.UUIDField(unique=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="outbox_events")
    event_type = models.CharField(max_length=80, choices=EventType.choices)
    aggregate_type = models.CharField(max_length=80)
    aggregate_id = models.CharField(max_length=120)
    deduplication_key = models.CharField(max_length=220)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    available_at = models.DateTimeField()
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=120, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=120, blank=True)
    last_error_message = models.CharField(max_length=500, blank=True)
    result_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["available_at", "created_at"]
        indexes = [
            models.Index(fields=["status", "available_at"]),
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["event_type", "status"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["aggregate_type", "aggregate_id"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["deduplication_key"], name="unique_outbox_deduplication_key"),
        ]

    def __str__(self):
        return f"{self.tenant.slug} / {self.event_type} / {self.event_id} / {self.status}"
