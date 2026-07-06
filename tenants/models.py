from django.db import models


class Tenant(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True)
    domain = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AssistantProfile(models.Model):
    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name="assistant_profile",
    )
    name = models.CharField(max_length=80, default="Lívia")
    initial_message = models.TextField(
        default="Olá! Sou a Lívia. Como posso te ajudar?"
    )
    tone = models.CharField(max_length=120, default="consultivo, claro e profissional")
    primary_goal = models.CharField(max_length=160, default="qualificar leads")
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.tenant.name}"
