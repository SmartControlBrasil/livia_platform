from django.db import models

from tenants.models import Tenant


class TenantRagConfiguration(models.Model):
    class InventoryStatus(models.TextChoices):
        IDLE = "idle", "Idle"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name="rag_configuration",
    )
    approved_folder_id = models.CharField(max_length=120)
    sync_enabled = models.BooleanField(default=False)
    last_inventory_status = models.CharField(
        max_length=20,
        choices=InventoryStatus.choices,
        default=InventoryStatus.IDLE,
    )
    last_inventory_at = models.DateTimeField(null=True, blank=True)
    last_inventory_started_at = models.DateTimeField(null=True, blank=True)
    last_inventory_mode = models.CharField(max_length=30, blank=True)
    last_inventory_file_count = models.PositiveIntegerField(default=0)
    last_inventory_error = models.CharField(max_length=500, blank=True)
    last_index_status = models.CharField(
        max_length=20,
        choices=InventoryStatus.choices,
        default=InventoryStatus.IDLE,
    )
    last_index_at = models.DateTimeField(null=True, blank=True)
    last_index_started_at = models.DateTimeField(null=True, blank=True)
    last_index_mode = models.CharField(max_length=30, blank=True)
    last_index_run_id = models.CharField(max_length=64, blank=True)
    last_index_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant__slug"]
        indexes = [
            models.Index(fields=["sync_enabled"]),
            models.Index(fields=["last_inventory_status"]),
            models.Index(fields=["last_index_status"]),
        ]

    def __str__(self):
        return f"{self.tenant.slug} / {self.approved_folder_id}"


class TenantRagDriveFileManifest(models.Model):
    class Status(models.TextChoices):
        DISCOVERED = "discovered", "Discovered"
        EXPORTED = "exported", "Exported"
        UPDATED = "updated", "Updated"
        UNCHANGED = "unchanged", "Unchanged"
        SKIPPED_UNSUPPORTED = "skipped_unsupported", "Skipped unsupported"
        FAILED = "failed", "Failed"
        REMOVED = "removed", "Removed"
        UNAVAILABLE = "unavailable", "Unavailable"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="rag_drive_file_manifests",
    )
    configuration = models.ForeignKey(
        TenantRagConfiguration,
        on_delete=models.CASCADE,
        related_name="drive_file_manifests",
    )
    drive_file_id = models.CharField(max_length=120)
    name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=160)
    relative_path = models.CharField(max_length=600, blank=True)
    drive_modified_time = models.DateTimeField(null=True, blank=True)
    drive_size_bytes = models.BigIntegerField(null=True, blank=True)
    normalized_text_sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DISCOVERED)
    is_active = models.BooleanField(default=True)
    first_discovered_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    last_exported_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant__slug", "relative_path", "name", "drive_file_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "drive_file_id"],
                name="unique_rag_drive_file_per_tenant",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "is_active"]),
            models.Index(fields=["tenant", "last_seen_at"]),
        ]

    def __str__(self):
        return f"{self.tenant.slug} / {self.drive_file_id} / {self.status}"


class TenantRagDriveTextStaging(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="rag_drive_text_staging",
    )
    manifest = models.OneToOneField(
        TenantRagDriveFileManifest,
        on_delete=models.CASCADE,
        related_name="text_staging",
    )
    normalized_text = models.TextField(blank=True)
    normalized_text_sha256 = models.CharField(max_length=64)
    normalized_text_char_count = models.PositiveIntegerField(default=0)
    normalized_text_byte_count = models.PositiveIntegerField(default=0)
    exported_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant__slug", "-updated_at"]
        indexes = [
            models.Index(fields=["tenant", "updated_at"]),
        ]

    def __str__(self):
        return f"{self.tenant.slug} / {self.manifest.drive_file_id}"


class TenantRagDocumentChunk(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REPLACED = "replaced", "Replaced"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="rag_document_chunks",
    )
    manifest = models.ForeignKey(
        TenantRagDriveFileManifest,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    staging = models.ForeignKey(
        TenantRagDriveTextStaging,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    ordinal = models.PositiveIntegerField()
    chunk_text = models.TextField(blank=True)
    chunk_sha256 = models.CharField(max_length=64)
    source_text_sha256 = models.CharField(max_length=64)
    chunk_config_signature = models.CharField(max_length=64)
    char_count = models.PositiveIntegerField(default=0)
    byte_count = models.PositiveIntegerField(default=0)
    start_char = models.PositiveIntegerField(default=0)
    end_char = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant__slug", "manifest_id", "ordinal"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "manifest", "source_text_sha256", "chunk_config_signature", "ordinal"],
                name="unique_rag_chunk_per_document_version_ordinal",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "manifest", "is_active"]),
            models.Index(fields=["tenant", "source_text_sha256"]),
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self):
        return f"{self.tenant.slug} / {self.manifest.drive_file_id} / {self.ordinal}"

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.manifest_id and self.tenant_id and self.manifest.tenant_id != self.tenant_id:
            errors["manifest"] = "Manifest tenant must match chunk tenant."
        if self.staging_id and self.tenant_id and self.staging.tenant_id != self.tenant_id:
            errors["staging"] = "Staging tenant must match chunk tenant."
        if self.manifest_id and self.staging_id and self.staging.manifest_id != self.manifest_id:
            errors["staging"] = "Staging must belong to manifest."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class TenantRagChunkEmbedding(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REPLACED = "replaced", "Replaced"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="rag_chunk_embeddings",
    )
    chunk = models.ForeignKey(
        TenantRagDocumentChunk,
        on_delete=models.CASCADE,
        related_name="embeddings",
    )
    manifest = models.ForeignKey(
        TenantRagDriveFileManifest,
        on_delete=models.CASCADE,
        related_name="chunk_embeddings",
    )
    chunk_sha256 = models.CharField(max_length=64)
    chunk_config_signature = models.CharField(max_length=64)
    provider = models.CharField(max_length=40)
    model = models.CharField(max_length=120)
    dimension = models.PositiveIntegerField()
    embedding_config_signature = models.CharField(max_length=64)
    vector = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    is_active = models.BooleanField(default=True)
    first_indexed_at = models.DateTimeField(null=True, blank=True)
    last_indexed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant__slug", "chunk_id", "-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "chunk", "embedding_config_signature"],
                name="unique_rag_embedding_per_chunk_config",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "is_active", "status"]),
            models.Index(fields=["tenant", "embedding_config_signature"]),
            models.Index(fields=["tenant", "chunk", "is_active"]),
            models.Index(fields=["tenant", "manifest"]),
        ]

    def __str__(self):
        return f"{self.tenant.slug} / chunk={self.chunk_id} / {self.model}"

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.chunk_id and self.tenant_id and self.chunk.tenant_id != self.tenant_id:
            errors["chunk"] = "Chunk tenant must match embedding tenant."
        if self.manifest_id and self.tenant_id and self.manifest.tenant_id != self.tenant_id:
            errors["manifest"] = "Manifest tenant must match embedding tenant."
        if self.chunk_id and self.manifest_id and self.chunk.manifest_id != self.manifest_id:
            errors["manifest"] = "Manifest must belong to chunk."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class TenantRagIndexRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="rag_index_runs",
    )
    run_id = models.CharField(max_length=64)
    mode = models.CharField(max_length=30)
    provider = models.CharField(max_length=40, blank=True)
    model = models.CharField(max_length=120, blank=True)
    dimension = models.PositiveIntegerField(default=0)
    embedding_config_signature = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    dry_run = models.BooleanField(default=False)
    documents = models.PositiveIntegerField(default=0)
    chunks = models.PositiveIntegerField(default=0)
    pending = models.PositiveIntegerField(default=0)
    indexed = models.PositiveIntegerField(default=0)
    reindexed = models.PositiveIntegerField(default=0)
    unchanged = models.PositiveIntegerField(default=0)
    deactivated = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    batches = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "run_id"],
                name="unique_rag_index_run_per_tenant",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "started_at"]),
        ]

    def __str__(self):
        return f"{self.tenant.slug} / {self.run_id} / {self.status}"


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
    source_type = models.CharField(max_length=40, default="manual")
    source_url = models.URLField(blank=True)
    tags = models.JSONField(default=list, blank=True)
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

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    def __str__(self):
        return f"{self.title} / {self.tenant.slug}"
