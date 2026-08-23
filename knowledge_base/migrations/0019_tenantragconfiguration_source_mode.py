from django.db import migrations, models


def infer_source_mode(apps, schema_editor):
    TenantRagConfiguration = apps.get_model("knowledge_base", "TenantRagConfiguration")
    TenantRagConfiguration.objects.filter(approved_folder_id__gt="").update(source_mode="google_drive")


def reverse_infer_source_mode(apps, schema_editor):
    TenantRagConfiguration = apps.get_model("knowledge_base", "TenantRagConfiguration")
    TenantRagConfiguration.objects.update(source_mode="manual")


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge_base", "0018_knowledgedocument_content_sha256_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantragconfiguration",
            name="source_mode",
            field=models.CharField(
                choices=[("manual", "Manual/local"), ("google_drive", "Google Drive")],
                default="manual",
                help_text="Origem operacional do conhecimento: manual/local ou Google Drive.",
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="tenantragconfiguration",
            name="approved_folder_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="ID da pasta aprovada no Google Drive. Opcional para conhecimento manual/local.",
                max_length=120,
            ),
        ),
        migrations.AddIndex(
            model_name="tenantragconfiguration",
            index=models.Index(fields=["source_mode"], name="knowledge_b_source__489dfc_idx"),
        ),
        migrations.RunPython(infer_source_mode, reverse_infer_source_mode),
    ]
