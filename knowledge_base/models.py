from django.db import models

from tenants.models import Tenant


class KnowledgeDocument(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DRAFT = "draft", "Draft"
        ARCHIVED = "archived", "Archived"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="knowledge_documents",
    )

    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=120)
    content = models.TextField(blank=True)
    source_url = models.URLField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "slug"],
                name="unique_knowledge_document_per_tenant_slug",
            )
        ]

    def __str__(self):
        return f"{self.title} / {self.tenant.slug}"
