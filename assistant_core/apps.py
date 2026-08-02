from django.apps import AppConfig


class AssistantCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "assistant_core"

    def ready(self):
        import config.checks  # noqa: F401 — registra system checks de staging
