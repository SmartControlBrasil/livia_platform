from django.conf import settings
from django.db import models

from conversations.models import Conversation
from tenants.models import Tenant


class LeadDraft(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUALIFIED = "qualified", "Qualified"
        SENT_TO_CRM = "sent_to_crm", "Sent to CRM"
        FAILED = "failed", "Failed"

    class QualificationStatus(models.TextChoices):
        NEW = "new", "New"
        IN_PROGRESS = "in_progress", "In progress"
        QUALIFIED = "qualified", "Qualified"
        DISQUALIFIED = "disqualified", "Disqualified"

    class HandoffStatus(models.TextChoices):
        NOT_REQUESTED = "not_requested", "Not requested"
        READY = "ready", "Ready"
        REQUESTED = "requested", "Requested"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class DispatchStatus(models.TextChoices):
        NOT_QUEUED = "not_queued", "Not queued"
        PENDING = "pending", "Pending"
        DRY_RUN = "dry_run", "Dry run"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"
        RETRYING = "retrying", "Retrying"

    class CommercialStatus(models.TextChoices):
        NEW = "new", "Novo"
        CONTACT_PENDING = "contact_pending", "Contato pendente"
        IN_PROGRESS = "in_progress", "Em atendimento"
        QUALIFIED = "qualified", "Qualificado"
        WON = "won", "Ganho"
        LOST = "lost", "Perdido"
        CLOSED = "closed", "Encerrado"

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
    qualification_data = models.JSONField(default=dict, blank=True)
    field_sources = models.JSONField(default=dict, blank=True)
    qualification_policy = models.CharField(max_length=80, blank=True)
    qualification_status = models.CharField(
        max_length=30,
        choices=QualificationStatus.choices,
        default=QualificationStatus.NEW,
    )
    handoff_status = models.CharField(
        max_length=30,
        choices=HandoffStatus.choices,
        default=HandoffStatus.NOT_REQUESTED,
    )
    dispatch_status = models.CharField(
        max_length=30,
        choices=DispatchStatus.choices,
        default=DispatchStatus.NOT_QUEUED,
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    commercial_status = models.CharField(
        max_length=30,
        choices=CommercialStatus.choices,
        default=CommercialStatus.NEW,
        db_index=True,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_leads",
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    first_human_action_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.CharField(max_length=120, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    crm_external_id = models.CharField(max_length=120, blank=True)
    crm_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_to_crm_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["tenant", "commercial_status", "-updated_at"]),
            models.Index(fields=["tenant", "assigned_to", "-updated_at"]),
        ]

    def __str__(self):
        label = self.name or self.company or self.email or "Lead sem identificação"
        return f"{label} / {self.tenant.slug}"


class CommercialNote(models.Model):
    """Nota interna de atendimento — nunca exposta ao visitante."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="commercial_notes")
    lead_draft = models.ForeignKey(
        LeadDraft,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="commercial_notes",
    )
    handoff = models.ForeignKey(
        "conversations.HandoffRequest",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="commercial_notes",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commercial_notes",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        target = f"lead:{self.lead_draft_id}" if self.lead_draft_id else f"handoff:{self.handoff_id}"
        return f"Nota {target} @ {self.created_at:%Y-%m-%d %H:%M}"
