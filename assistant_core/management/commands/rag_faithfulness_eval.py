from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from assistant_core.eval.response_runner import ResponseFaithfulnessRunner, load_response_eval_cases
from tenants.models import AssistantProfile, Tenant


class Command(BaseCommand):
    help = "Avalia faithfulness de respostas grounded (sem LLM judge)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)
        parser.add_argument(
            "--dataset",
            default="knowledge_base/rag/eval/datasets/granimarmores_staging.json",
        )

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=str(options["tenant"]).strip()).first()
        if tenant is None:
            raise CommandError("Tenant not found.")
        profile = AssistantProfile.objects.filter(tenant=tenant, is_active=True).first()
        if profile is None:
            raise CommandError("Active assistant profile not found.")

        cases = load_response_eval_cases(__import__("pathlib").Path(options["dataset"]))
        if not cases:
            raise CommandError("No response_eval cases found in dataset.")

        runner = ResponseFaithfulnessRunner(tenant=tenant, assistant_profile=profile)
        results = [runner.run_case(case) for case in cases]
        summary = runner.summarize(results)

        self.stdout.write(f"Tenant: {tenant.slug}")
        self.stdout.write(f"Cases: {len(cases)}")
        self.stdout.write(f"faithfulness: {summary['faithfulness']}")
        self.stdout.write(f"ai_used: {summary['ai_used']}")
        self.stdout.write(f"wrong_vertical: {summary['wrong_vertical']}")
        for row in results:
            self.stdout.write(
                f"- {row.case_id} expect={row.expect} retrieval={row.retrieval_status} "
                f"ai={row.ai_status} faith={row.faithfulness} vertical_ok={not row.wrong_vertical} "
                f"reply={row.reply[:120]!r}"
            )
