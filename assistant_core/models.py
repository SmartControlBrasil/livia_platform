from django.db import models


class AiUsageEvent(models.Model):
    class Operation(models.TextChoices):
        CHAT_COMPLETION = "chat_completion", "Chat completion"
        GROUNDED_SYNTHESIS = "grounded_synthesis", "Grounded synthesis"
        EMBEDDING = "embedding", "Embedding"

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_usage_events",
    )
    operation = models.CharField(max_length=40, choices=Operation.choices)
    model = models.CharField(max_length=80, blank=True)
    success = models.BooleanField(default=False)
    error_type = models.CharField(max_length=80, blank=True)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["operation", "created_at"]),
        ]

    def __str__(self):
        return f"{self.operation} / {self.model} / tokens={self.total_tokens}"
