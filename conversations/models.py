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
