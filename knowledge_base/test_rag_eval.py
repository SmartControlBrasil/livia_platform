from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings

from django.utils import timezone

from knowledge_base.models import TenantRagConfiguration, TenantRagDocumentChunk, TenantRagDriveFileManifest, TenantRagDriveTextStaging
from knowledge_base.rag.eval.runner import EvalCase, EvalCaseResult, EvalReport, run_eval_for_tenant
from knowledge_base.rag.embeddings import FakeEmbeddingProvider
from tenants.models import Tenant


@override_settings(
    LIVIA_RAG_ENABLED=True,
    LIVIA_RAG_DRY_RUN=True,
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-eval",
    LIVIA_RAG_EMBEDDING_DIMENSION=8,
    LIVIA_RAG_MIN_SIMILARITY_SCORE=0.25,
    LIVIA_RAG_MAX_RETRIEVED_CHUNKS=5,
)
class RagEvalMetricsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Eval", slug="eval-tenant")
        self.config = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="folder",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        self.provider = FakeEmbeddingProvider()

    def _chunk(self, file_id: str, text: str):
        manifest = TenantRagDriveFileManifest.objects.create(
            tenant=self.tenant,
            configuration=self.config,
            drive_file_id=file_id,
            name=file_id,
            mime_type="application/vnd.google-apps.document",
            status=TenantRagDriveFileManifest.Status.EXPORTED,
            is_active=True,
        )
        staging = TenantRagDriveTextStaging.objects.create(
            tenant=self.tenant,
            manifest=manifest,
            normalized_text=text,
            normalized_text_sha256="a" * 64,
            normalized_text_char_count=len(text),
            normalized_text_byte_count=len(text.encode("utf-8")),
            exported_at=timezone.now(),
        )
        return TenantRagDocumentChunk.objects.create(
            tenant=self.tenant,
            manifest=manifest,
            staging=staging,
            ordinal=0,
            chunk_text=text,
            chunk_sha256=f"{file_id}-hash".ljust(64, "0")[:64],
            source_text_sha256="a" * 64,
            chunk_config_signature="cfg",
            char_count=len(text),
            byte_count=len(text.encode("utf-8")),
            start_char=0,
            end_char=len(text),
            status=TenantRagDocumentChunk.Status.ACTIVE,
            is_active=True,
        )

    def test_confusion_matrix_and_precision_recall(self):
        synthetic = EvalReport(
            tenant_slug=self.tenant.slug,
            total=4,
            results=[
                EvalCaseResult(
                    "a",
                    "hit",
                    "completed",
                    True,
                    "src",
                    0.9,
                    1,
                    True,
                    True,
                    True,
                ),
                EvalCaseResult(
                    "b",
                    "empty",
                    "empty",
                    False,
                    "",
                    0.1,
                    1,
                    True,
                    True,
                    True,
                ),
                EvalCaseResult(
                    "c",
                    "hit",
                    "empty",
                    False,
                    "",
                    0.1,
                    1,
                    False,
                    False,
                    False,
                ),
                EvalCaseResult(
                    "d",
                    "empty",
                    "completed",
                    True,
                    "src",
                    0.5,
                    1,
                    False,
                    False,
                    False,
                ),
            ],
        )
        summary = synthetic.summary()
        self.assertEqual(summary["correct_hit"], 1)
        self.assertEqual(summary["correct_empty"], 1)
        self.assertEqual(summary["false_hit"], 1)
        self.assertEqual(summary["false_empty"], 1)
        self.assertAlmostEqual(summary["precision"], 0.5)
        self.assertAlmostEqual(summary["recall"], 0.5)

    def test_rag_eval_command_compare_thresholds(self):
        self._chunk("doc-b", "granito preto absoluto")
        with override_settings(LIVIA_RAG_INDEXING_ENABLED=True):
            call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        out = StringIO()
        call_command(
            "rag_eval",
            "--tenant",
            self.tenant.slug,
            "--compare-thresholds",
            "0.20,0.25",
            stdout=out,
        )
        text = out.getvalue()
        self.assertIn("precision", text)
        self.assertIn("0.20", text)

    def test_rag_eval_uses_tenant_threshold_by_default(self):
        self._chunk("doc-thr", "granito preto absoluto para cozinha")
        self.config.min_similarity_score = 0.35
        self.config.save(update_fields=["min_similarity_score", "updated_at"])
        with override_settings(LIVIA_RAG_INDEXING_ENABLED=True):
            call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        dataset = Path("knowledge_base/rag/eval/datasets/tmp_eval_threshold.json")
        dataset.write_text(
            json.dumps(
                [
                    {
                        "id": "hit",
                        "query": "granito preto absoluto",
                        "expect": "hit",
                        "expected_source_contains": ["granito"],
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            out = StringIO()
            call_command(
                "rag_eval",
                "--tenant",
                self.tenant.slug,
                "--dataset",
                str(dataset),
                stdout=out,
            )
            text = out.getvalue()
            self.assertIn("effective_threshold: 0.35 (source=tenant)", text)
        finally:
            dataset.unlink(missing_ok=True)
