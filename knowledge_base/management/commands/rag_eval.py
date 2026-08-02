from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from knowledge_base.rag.eval.runner import load_eval_dataset, run_eval_for_tenant
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Avalia retrieval RAG com dataset controlado (sem chamar IA generativa)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant.")
        parser.add_argument(
            "--dataset",
            default="knowledge_base/rag/eval/datasets/granimarmores_staging.json",
            help="Caminho JSON relativo ao projeto.",
        )
        parser.add_argument(
            "--threshold",
            type=float,
            default=None,
            help="Override temporário de LIVIA_RAG_MIN_SIMILARITY_SCORE.",
        )
        parser.add_argument(
            "--max-chunks",
            type=int,
            default=None,
            help="Override temporário de LIVIA_RAG_MAX_RETRIEVED_CHUNKS.",
        )
        parser.add_argument(
            "--compare-thresholds",
            default="",
            help="Lista separada por vírgula, ex.: 0.20,0.25,0.30",
        )
        parser.add_argument(
            "--compare-max-chunks",
            default="",
            help="Lista separada por vírgula, ex.: 3,5",
        )

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"]).strip()
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError("Tenant not found.")

        dataset_path = Path(options["dataset"])
        if not dataset_path.is_file():
            raise CommandError(f"Dataset not found: {dataset_path}")

        cases = load_eval_dataset(dataset_path)
        compare = str(options.get("compare_thresholds") or "").strip()
        thresholds = [float(item.strip()) for item in compare.split(",") if item.strip()]
        compare_k = str(options.get("compare_max_chunks") or "").strip()
        max_chunk_variants = [int(item.strip()) for item in compare_k.split(",") if item.strip()]

        def run_once(threshold: float | None, max_chunks: int | None):
            return run_eval_for_tenant(
                tenant=tenant,
                cases=cases,
                threshold=threshold,
                max_chunks=max_chunks,
            )

        if max_chunk_variants:
            self.stdout.write(f"Tenant: {tenant.slug} | cases: {len(cases)}")
            self.stdout.write("max_chunks | top1 | topk | avg_context_chars | avg_selected")
            base_threshold = options.get("threshold")
            for max_chunks in max_chunk_variants:
                report = run_once(base_threshold, max_chunks)
                summary = report.summary()
                self.stdout.write(
                    f"{max_chunks} | {summary['top1_source_accuracy'] * 100:.1f}% | "
                    f"{summary['topk_source_accuracy'] * 100:.1f}% | "
                    f"{summary['avg_context_chars']:.0f} | {summary['avg_selected_count']:.2f}"
                )
            return

        if thresholds:
            self.stdout.write(f"Tenant: {tenant.slug} | cases: {len(cases)}")
            self.stdout.write(
                "threshold | TP | FP | FN | TN | precision | recall | top1 | topk | hit_rate"
            )
            for threshold in thresholds:
                report = run_once(threshold, options.get("max_chunks"))
                summary = report.summary()
                self.stdout.write(
                    f"{threshold:.2f} | {summary['correct_hit']} | {summary['false_hit']} | "
                    f"{summary['false_empty']} | {summary['correct_empty']} | "
                    f"{summary['precision'] * 100:.1f}% | {summary['recall'] * 100:.1f}% | "
                    f"{summary['top1_source_accuracy'] * 100:.1f}% | "
                    f"{summary['topk_source_accuracy'] * 100:.1f}% | "
                    f"{summary['hit_rate'] * 100:.1f}%"
                )
            return

        report = run_once(options.get("threshold"), options.get("max_chunks"))
        summary = report.summary()
        matrix = report.confusion_matrix()
        scores = report.score_distribution()

        self.stdout.write(f"Tenant: {report.tenant_slug}")
        self.stdout.write(f"Cases: {summary['total_cases']}")
        self.stdout.write(
            f"effective_threshold: {summary['effective_threshold']:.2f} (source={summary['threshold_source']})"
        )
        self.stdout.write(f"executed: {summary['executed']} skipped: {summary['skipped']}")
        self.stdout.write(f"hit_rate: {summary['hit_rate'] * 100:.1f}%")
        self.stdout.write(f"empty_rate: {summary['empty_rate'] * 100:.1f}%")
        self.stdout.write(f"failure_rate: {summary['failure_rate'] * 100:.1f}%")
        self.stdout.write(
            f"expected_hit_success: {summary['expected_hit_success']}/{summary['expected_hit_total']}"
        )
        self.stdout.write(
            f"expected_empty_success: {summary['expected_empty_success']}/{summary['expected_empty_total']}"
        )
        self.stdout.write("confusion (retrieval returned HIT vs expect):")
        self.stdout.write(f"  TP={matrix['tp']} FP={matrix['fp']} FN={matrix['fn']} TN={matrix['tn']}")
        self.stdout.write(f"precision: {summary['precision'] * 100:.1f}%")
        self.stdout.write(f"recall: {summary['recall'] * 100:.1f}%")
        self.stdout.write(f"top1_source_accuracy: {summary['top1_source_accuracy'] * 100:.1f}%")
        self.stdout.write(f"topk_source_accuracy: {summary['topk_source_accuracy'] * 100:.1f}%")
        self.stdout.write(f"mrr: {summary['mrr']:.3f}")
        self.stdout.write(f"avg_latency_ms: {summary['avg_latency_ms']:.1f}")
        self.stdout.write(f"p50_latency_ms: {summary['p50_latency_ms']}")
        self.stdout.write(f"p95_latency_ms: {summary['p95_latency_ms']}")
        self.stdout.write(
            f"embedding_latency_ms: avg={summary['embedding_avg_ms']:.1f} p50={summary['embedding_p50_ms']} p95={summary['embedding_p95_ms']}"
        )
        self.stdout.write(
            f"vector_search_latency_ms: avg={summary['vector_search_avg_ms']:.1f} p50={summary['vector_search_p50_ms']} p95={summary['vector_search_p95_ms']}"
        )
        self.stdout.write(
            f"postprocess_latency_ms: avg={summary['postprocess_avg_ms']:.1f} p50={summary['postprocess_p50_ms']} p95={summary['postprocess_p95_ms']}"
        )
        self.stdout.write(f"avg_max_score: {summary['avg_max_score']:.3f}")
        self.stdout.write(f"avg_context_chars: {summary['avg_context_chars']:.1f}")
        self.stdout.write(f"avg_retrieved_chars: {summary['avg_retrieved_chars']:.1f}")
        self.stdout.write(f"avg_selected_raw_chars: {summary['avg_selected_raw_chars']:.1f}")
        self.stdout.write(f"avg_selected_chars: {summary['avg_selected_chars']:.1f}")
        self.stdout.write(f"avg_formatted_context_chars: {summary['avg_formatted_context_chars']:.1f}")
        self.stdout.write(f"avg_chunks_discarded_by_budget: {summary['avg_chunks_discarded_by_budget']:.2f}")
        self.stdout.write(f"avg_candidate_count: {summary['avg_candidate_count']:.1f}")
        self.stdout.write(f"avg_selected_count: {summary['avg_selected_count']:.2f}")
        self.stdout.write("score_distribution:")
        for group, stats in scores.items():
            self.stdout.write(
                f"  {group}: n={int(stats['count'])} min={stats['min']:.3f} "
                f"mean={stats['mean']:.3f} median={stats['median']:.3f} max={stats['max']:.3f}"
            )

        problematic = [
            r
            for r in report.results
            if (r.expect == "hit" and not r.hit)
            or (r.expect == "hit" and r.hit and not r.topk_source_match)
            or (r.expect == "empty" and r.hit)
        ]
        if problematic:
            self.stdout.write("problem_queries:")
            for row in problematic:
                self.stdout.write(
                    f"  {row.case_id} expect={row.expect} status={row.status} hit={row.hit} "
                    f"score={row.max_score:.3f} top={row.top_source[:80]}"
                )
