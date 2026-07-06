from django.db import models

from conversations.models import Conversation
from tenants.models import Tenant


class LeadDraft(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUALIFIED = "qualified", "Qualified"
        SENT_TO_CRM = "sent_to_crm", "Sent to CRM"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="lead_drafts",
    )
    conversation = models.OneToOneField(
        Conversation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lead_draft",
    )

    name = models.CharField(max_length=120, blank=True)
    company = models.CharField(max_length=160, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    city = models.CharField(max_length=120, blank=True)
    need_summary = models.TextField(blank=True)

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    crm_external_id = models.CharField(max_length=120, blank=True)
    crm_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_to_crm_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        label = self.name or self.company or self.email or "Lead sem identificação"
        return f"{label} / {self.tenant.slug}"
