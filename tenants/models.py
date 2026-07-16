from django.core.validators import RegexValidator
from django.db import models


WIDGET_POSITION_BOTTOM_RIGHT = "bottom_right"
WIDGET_POSITION_BOTTOM_LEFT = "bottom_left"
WIDGET_POSITION_CHOICES = (
    (WIDGET_POSITION_BOTTOM_RIGHT, "Bottom right"),
    (WIDGET_POSITION_BOTTOM_LEFT, "Bottom left"),
)

DEFAULT_WIDGET_LAUNCHER_LABEL = "Fale com a Lívia"
DEFAULT_WIDGET_PRIMARY_COLOR = "#2563eb"
DEFAULT_WIDGET_PLACEHOLDER_TEXT = "Digite sua mensagem..."

validate_widget_color = RegexValidator(
    regex=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
    message="Primary color must be a hex color in #RGB or #RRGGBB format.",
)


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
    use_ai = models.BooleanField(default=False)
    widget_title = models.CharField(max_length=80, blank=True)
    launcher_label = models.CharField(max_length=80, default=DEFAULT_WIDGET_LAUNCHER_LABEL)
    primary_color = models.CharField(
        max_length=7,
        default=DEFAULT_WIDGET_PRIMARY_COLOR,
        validators=[validate_widget_color],
    )
    position = models.CharField(
        max_length=20,
        choices=WIDGET_POSITION_CHOICES,
        default=WIDGET_POSITION_BOTTOM_RIGHT,
    )
    show_branding = models.BooleanField(default=True)
    collect_contact_hint = models.CharField(max_length=160, blank=True)
    placeholder_text = models.CharField(max_length=120, default=DEFAULT_WIDGET_PLACEHOLDER_TEXT)
    is_widget_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.tenant.name}"

    @property
    def effective_widget_title(self):
        return self.widget_title.strip() or self.name
