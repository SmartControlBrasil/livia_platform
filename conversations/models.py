from django.db import models

from tenants.models import Tenant


class Conversation(models.Model):
    class LeadState(models.TextChoices):
        DISCOVERY = "discovery", "Discovery"
        OFFER_HANDOFF = "offer_handoff", "Offer handoff"
        COLLECT_NEED = "collect_need", "Collect need"
        COLLECT_NAME_COMPANY = "collect_name_company", "Collect name/company"
        COLLECT_CONTACT = "collect_contact", "Collect contact"
        QUALIFIED = "qualified", "Qualified"
        CLOSED = "closed", "Closed"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    session_id = models.CharField(max_length=120)
    visitor_name = models.CharField(max_length=120, blank=True)
    visitor_email = models.EmailField(blank=True)
    visitor_phone = models.CharField(max_length=40, blank=True)
    source_page = models.URLField(blank=True)
    is_qualified = models.BooleanField(default=False)
    lead_state = models.CharField(
        max_length=40,
        choices=LeadState.choices,
        default=LeadState.DISCOVERY,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["tenant", "session_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "session_id"],
                name="unique_conversation_per_tenant_session",
            )
        ]

    def __str__(self):
        return f"{self.tenant.slug} / {self.session_id}"


class ChatRequest(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="chat_requests",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_requests",
    )
    session_id = models.CharField(max_length=120)
    request_id = models.UUIDField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING,
    )
    request_fingerprint = models.CharField(max_length=64)
    response_payload = models.JSONField(default=dict, blank=True)
    response_status_code = models.PositiveSmallIntegerField(default=200)
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["status", "updated_at"]),
            models.Index(fields=["tenant", "session_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "session_id", "request_id"],
                name="unique_chat_request_per_tenant_session_request",
            )
        ]

    def __str__(self):
        return f"{self.tenant.slug} / {self.session_id} / {self.request_id} / {self.status}"


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"


class HandoffRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        RESOLVED = "resolved", "Resolved"
        CANCELLED = "cancelled", "Cancelled"

    class Reason(models.TextChoices):
        EXPLICIT_REQUEST = "explicit_request", "Explicit request"
        QUALIFIED_LEAD = "qualified_lead", "Qualified lead"
        TECHNICAL_COMPLEXITY = "technical_complexity", "Technical complexity"
        SUPPORT_REQUEST = "support_request", "Support request"
        EMERGENCY_OR_URGENT = "emergency_or_urgent", "Emergency or urgent"
        MANUAL = "manual", "Manual"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="handoff_requests",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="handoff_requests",
    )
    lead_draft = models.ForeignKey(
        "leads.LeadDraft",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handoff_requests",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reason = models.CharField(max_length=40, choices=Reason.choices, default=Reason.MANUAL)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    visitor_name = models.CharField(max_length=120, blank=True)
    visitor_company = models.CharField(max_length=160, blank=True)
    visitor_phone = models.CharField(max_length=40, blank=True)
    visitor_email = models.EmailField(blank=True)
    summary = models.TextField(blank=True)
    source_page = models.URLField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["conversation", "status"]),
        ]

    def __str__(self):
        return f"Handoff #{self.pk} / {self.tenant.slug} / {self.status}"
