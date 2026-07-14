from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="assistantprofile",
            name="use_ai",
            field=models.BooleanField(default=False),
        ),
    ]
