from django.core.management import call_command
from django.test import TestCase

from tenants.models import AssistantProfile, Tenant


class SeedInitialTenantsCommandTests(TestCase):
    def test_seed_initial_tenants_is_idempotent(self):
        call_command("seed_initial_tenants", verbosity=0)
        call_command("seed_initial_tenants", verbosity=0)

        self.assertEqual(Tenant.objects.filter(slug="smart-control-brasil").count(), 1)
        self.assertEqual(Tenant.objects.filter(slug="granimarmores-pitondo").count(), 1)
        self.assertEqual(AssistantProfile.objects.count(), 2)
        smart_tenant = Tenant.objects.get(slug="smart-control-brasil")
        self.assertTrue(smart_tenant.is_active)
        self.assertIn("Smart Control Brasil", smart_tenant.assistant_profile.initial_message)
