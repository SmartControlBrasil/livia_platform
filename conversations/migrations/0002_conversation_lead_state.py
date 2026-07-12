from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="lead_state",
            field=models.CharField(
                choices=[
                    ("discovery", "Discovery"),
                    ("offer_handoff", "Offer handoff"),
                    ("collect_need", "Collect need"),
                    ("collect_name_company", "Collect name/company"),
                    ("collect_contact", "Collect contact"),
                    ("qualified", "Qualified"),
                    ("closed", "Closed"),
                ],
                default="discovery",
                max_length=40,
            ),
        ),
    ]
