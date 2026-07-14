# Generated for Livia Platform knowledge base phase 15

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge_base", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="knowledgedocument",
            name="source_type",
            field=models.CharField(default="manual", max_length=40),
        ),
        migrations.AddField(
            model_name="knowledgedocument",
            name="tags",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
