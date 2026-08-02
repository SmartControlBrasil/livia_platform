from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0006_tenantallowedorigin"),
    ]

    operations = [
        migrations.AddField(
            model_name="assistantprofile",
            name="business_name",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="assistantprofile",
            name="business_domain",
            field=models.CharField(blank=True, default="", max_length=220),
        ),
        migrations.AddField(
            model_name="assistantprofile",
            name="short_description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="assistantprofile",
            name="grounded_synthesis_enabled",
            field=models.BooleanField(default=False),
        ),
    ]
