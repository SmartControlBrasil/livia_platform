# Generated for Livia Platform handoff phase 16

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0002_conversation_lead_state"),
        ("leads", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="HandoffRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("sent", "Sent"), ("resolved", "Resolved"), ("cancelled", "Cancelled")], default="pending", max_length=20)),
                ("reason", models.CharField(choices=[("explicit_request", "Explicit request"), ("qualified_lead", "Qualified lead"), ("technical_complexity", "Technical complexity"), ("support_request", "Support request"), ("emergency_or_urgent", "Emergency or urgent"), ("manual", "Manual")], default="manual", max_length=40)),
                ("priority", models.CharField(choices=[("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")], default="normal", max_length=20)),
                ("visitor_name", models.CharField(blank=True, max_length=120)),
                ("visitor_company", models.CharField(blank=True, max_length=160)),
                ("visitor_phone", models.CharField(blank=True, max_length=40)),
                ("visitor_email", models.EmailField(blank=True, max_length=254)),
                ("summary", models.TextField(blank=True)),
                ("source_page", models.URLField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="handoff_requests", to="conversations.conversation")),
                ("lead_draft", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="handoff_requests", to="leads.leaddraft")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="handoff_requests", to="tenants.tenant")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["tenant", "status"], name="conversation_tenant__53fdfb_idx"), models.Index(fields=["conversation", "status"], name="conversation_convers_4b4dd1_idx")],
            },
        ),
    ]
