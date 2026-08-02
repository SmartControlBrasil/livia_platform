from unittest.mock import Mock, patch

from django.contrib import admin
from django.test import TestCase

from conversations.admin import mark_handoffs_cancelled, mark_handoffs_resolved
from conversations.models import Conversation, HandoffRequest, Message
from integrations.models import OutboxEvent
from knowledge_base.admin import activate_knowledge_documents, deactivate_knowledge_documents
from knowledge_base.models import KnowledgeDocument
from leads.admin import retry_failed_crm_dispatch
from leads.models import LeadDraft
from tenants.admin import (
    activate_tenants,
    deactivate_tenants,
    disable_profile_ai,
    enable_profile_ai,
)
from tenants.models import AssistantProfile, Tenant


class AdminRegistrationTests(TestCase):
    def test_core_models_are_registered_in_django_admin(self):
        for model in (
            Tenant,
            AssistantProfile,
            Conversation,
            Message,
            LeadDraft,
            KnowledgeDocument,
            HandoffRequest,
        ):
            self.assertIn(model, admin.site._registry)

    def test_admin_list_displays_include_operational_fields(self):
        self.assertEqual(admin.site._registry[Tenant].list_display, ["name", "slug", "domain", "is_active", "created_at", "updated_at"])
        self.assertIn("use_ai", admin.site._registry[AssistantProfile].list_display)
        self.assertIn("lead_state", admin.site._registry[Conversation].list_display)
        self.assertIn("lead_draft_status", admin.site._registry[Conversation].list_display)
        self.assertIn("handoff_status", admin.site._registry[Conversation].list_display)
        self.assertIn("short_content", admin.site._registry[Message].list_display)
        self.assertIn("service_area", admin.site._registry[LeadDraft].list_display)
        self.assertIn("crm_dispatch_state", admin.site._registry[LeadDraft].list_display)
        self.assertIn("sent_to_crm_at", admin.site._registry[LeadDraft].list_display)
        self.assertIn("is_active", admin.site._registry[KnowledgeDocument].list_display)
        self.assertIn("priority", admin.site._registry[HandoffRequest].list_display)
        self.assertIn("short_summary", admin.site._registry[HandoffRequest].list_display)


class AdminActionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil",
            domain="smartcontrolbrasil.com.br",
        )

    def test_tenant_actions_activate_and_deactivate(self):
        activate_tenants(None, None, Tenant.objects.filter(pk=self.tenant.pk))
        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.is_active)

        deactivate_tenants(None, None, Tenant.objects.filter(pk=self.tenant.pk))
        self.tenant.refresh_from_db()
        self.assertFalse(self.tenant.is_active)

    def test_assistant_profile_actions_enable_and_disable_ai(self):
        profile = AssistantProfile.objects.create(
            tenant=self.tenant,
            name="Lívia",
            primary_goal="qualificar leads",
            use_ai=False,
        )

        enable_profile_ai(None, None, AssistantProfile.objects.filter(pk=profile.pk))
        profile.refresh_from_db()
        self.assertTrue(profile.use_ai)

        disable_profile_ai(None, None, AssistantProfile.objects.filter(pk=profile.pk))
        profile.refresh_from_db()
        self.assertFalse(profile.use_ai)

    def test_knowledge_document_actions_activate_and_deactivate(self):
        document = KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="Documento operacional",
            slug="documento-operacional",
            content="Conteúdo de teste",
            status=KnowledgeDocument.Status.DRAFT,
        )

        activate_knowledge_documents(None, None, KnowledgeDocument.objects.filter(pk=document.pk))
        document.refresh_from_db()
        self.assertEqual(document.status, KnowledgeDocument.Status.ACTIVE)
        self.assertTrue(document.is_active)

        deactivate_knowledge_documents(None, None, KnowledgeDocument.objects.filter(pk=document.pk))
        document.refresh_from_db()
        self.assertEqual(document.status, KnowledgeDocument.Status.ARCHIVED)
        self.assertFalse(document.is_active)

    def test_handoff_action_marks_request_as_resolved(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="admin-handoff")
        handoff = HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=conversation,
            reason=HandoffRequest.Reason.EXPLICIT_REQUEST,
            priority=HandoffRequest.Priority.NORMAL,
        )

        mark_handoffs_resolved(None, None, HandoffRequest.objects.filter(pk=handoff.pk))
        handoff.refresh_from_db()

        self.assertEqual(handoff.status, HandoffRequest.Status.RESOLVED)
        self.assertIsNotNone(handoff.resolved_at)

    def test_handoff_action_marks_request_as_cancelled(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="admin-cancel")
        handoff = HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=conversation,
            reason=HandoffRequest.Reason.MANUAL,
            priority=HandoffRequest.Priority.LOW,
        )

        mark_handoffs_cancelled(None, None, HandoffRequest.objects.filter(pk=handoff.pk))
        handoff.refresh_from_db()

        self.assertEqual(handoff.status, HandoffRequest.Status.CANCELLED)

    def test_retry_failed_crm_dispatch_action_reprocesses_only_failed_without_external_id(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="admin-crm-retry")
        failed_lead = LeadDraft.objects.create(
            tenant=self.tenant,
            conversation=conversation,
            name="Maria",
            phone="11999998888",
            need_summary="Preciso de automação industrial.",
            status=LeadDraft.Status.FAILED,
            crm_error="erro temporário",
        )
        sent_lead = LeadDraft.objects.create(
            tenant=self.tenant,
            name="João",
            status=LeadDraft.Status.SENT_TO_CRM,
            crm_external_id="crm-existing",
        )
        inconsistent_lead = LeadDraft.objects.create(
            tenant=self.tenant,
            name="Ana",
            status=LeadDraft.Status.FAILED,
            crm_external_id="crm-already-created",
        )
        modeladmin = Mock()

        retry_failed_crm_dispatch(
            modeladmin,
            Mock(user=None, META={}),
            LeadDraft.objects.filter(pk__in=[failed_lead.pk, sent_lead.pk, inconsistent_lead.pk]),
        )

        failed_lead.refresh_from_db()
        sent_lead.refresh_from_db()
        inconsistent_lead.refresh_from_db()
        self.assertEqual(failed_lead.status, LeadDraft.Status.QUALIFIED)
        self.assertEqual(failed_lead.crm_error, "")
        self.assertEqual(sent_lead.status, LeadDraft.Status.SENT_TO_CRM)
        self.assertEqual(OutboxEvent.objects.filter(event_type=OutboxEvent.EventType.LEAD_QUALIFIED, aggregate_id=str(failed_lead.pk)).count(), 1)
        self.assertEqual(inconsistent_lead.status, LeadDraft.Status.FAILED)
        modeladmin.message_user.assert_called_once()

    def test_short_admin_text_methods_do_not_dump_large_values(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="admin-short")
        message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="Mensagem muito longa " * 20,
        )
        handoff = HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=conversation,
            summary="Resumo muito longo " * 20,
        )

        message_admin = admin.site._registry[Message]
        handoff_admin = admin.site._registry[HandoffRequest]

        self.assertLessEqual(len(message_admin.short_content(message)), 90)
        self.assertLessEqual(len(handoff_admin.short_summary(handoff)), 90)
