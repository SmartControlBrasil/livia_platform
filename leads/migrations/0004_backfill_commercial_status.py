# Generated manually for commercial operations backfill

from django.db import migrations


def forwards(apps, schema_editor):
    LeadDraft = apps.get_model("leads", "LeadDraft")
    LeadDraft.objects.filter(qualification_status="qualified").update(commercial_status="qualified")
    LeadDraft.objects.filter(qualification_status="disqualified").update(commercial_status="lost")
    LeadDraft.objects.filter(qualification_status="in_progress").update(commercial_status="in_progress")


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("leads", "0003_commercial_operations"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
