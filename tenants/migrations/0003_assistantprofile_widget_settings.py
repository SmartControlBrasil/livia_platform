from django.db import migrations, models
import tenants.models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0002_assistantprofile_use_ai"),
    ]

    operations = [
        migrations.AddField(
            model_name="assistantprofile",
            name="widget_title",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="assistantprofile",
            name="launcher_label",
            field=models.CharField(default="Fale com a Lívia", max_length=80),
        ),
        migrations.AddField(
            model_name="assistantprofile",
            name="primary_color",
            field=models.CharField(
                default="#2563eb",
                max_length=7,
                validators=[tenants.models.validate_widget_color],
            ),
        ),
        migrations.AddField(
            model_name="assistantprofile",
            name="position",
            field=models.CharField(
                choices=[("bottom_right", "Bottom right"), ("bottom_left", "Bottom left")],
                default="bottom_right",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="assistantprofile",
            name="show_branding",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="assistantprofile",
            name="collect_contact_hint",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="assistantprofile",
            name="placeholder_text",
            field=models.CharField(default="Digite sua mensagem...", max_length=120),
        ),
        migrations.AddField(
            model_name="assistantprofile",
            name="is_widget_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
