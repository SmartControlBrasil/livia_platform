from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from knowledge_base.rag.conversation_retrieval import retrieve_context
from knowledge_base.rag.embeddings import build_embedding_provider, load_embedding_config


@dataclass
class EvalCase:
    case_id: str
    query: str
    expect: str
    expected_source_contains: list[str] = field(default_factory=list)
    security_expectation: str = ""
    category: str = ""


@dataclass
class EvalCaseResult:
    case_id: str
    expect: str
    status: str
    hit: bool
    top_source: str
    max_score: float
    latency_ms: int
    source_match: bool
    topk_source_match: bool
    expect_success: bool
    context_chars: int = 0
    candidate_count: int = 0
    selected_count: int = 0
    expected_rank: int = 0
    embedding_ms: int = 0
    vector_search_ms: int = 0
    postprocess_ms: int = 0
    threshold: float = 0.0
    threshold_source: str = "global_default"
    retrieved_chars: int = 0
    selected_raw_chars: int = 0
    selected_chars: int = 0
    formatted_context_chars: int = 0
    chunks_discarded_by_budget: int = 0


@dataclass
class EvalReport:
    tenant_slug: str
    total: int
    results: list[EvalCaseResult]
    effective_threshold: float = 0.0
    threshold_source: str = "global_default"
    thresholds: list[float] = field(default_factory=list)
    max_chunks_variants: list[int] = field(default_factory=list)

    @property
    def latencies(self) -> list[int]:
        return [item.latency_ms for item in self.results]

    def confusion_matrix(self) -> dict[str, int]:
        tp = fp = fn = tn = 0
        for row in self.results:
            returned_hit = row.status == "completed" and row.hit
            expected_hit = row.expect == "hit"
            if expected_hit and returned_hit:
                tp += 1
            elif not expected_hit and returned_hit:
                fp += 1
            elif expected_hit and not returned_hit:
                fn += 1
            else:
                tn += 1
        return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}

    def retrieval_precision_recall(self) -> tuple[float, float]:
        m = self.confusion_matrix()
        precision = m["tp"] / (m["tp"] + m["fp"]) if (m["tp"] + m["fp"]) else 0.0
        recall = m["tp"] / (m["tp"] + m["fn"]) if (m["tp"] + m["fn"]) else 0.0
        return precision, recall

    def mean_reciprocal_rank(self) -> float:
        expected_hits = [r for r in self.results if r.expect == "hit"]
        if not expected_hits:
            return 0.0
        reciprocals = [1.0 / r.expected_rank if r.expected_rank > 0 else 0.0 for r in expected_hits]
        return statistics.mean(reciprocals)

    def score_distribution(self) -> dict[str, dict[str, float]]:
        groups: dict[str, list[float]] = {
            "relevant_correct": [],
            "relevant_incorrect": [],
            "expected_empty": [],
        }
        for row in self.results:
            if row.expect == "empty":
                groups["expected_empty"].append(row.max_score)
            elif row.expect_success:
                groups["relevant_correct"].append(row.max_score)
            elif row.expect == "hit":
                groups["relevant_incorrect"].append(row.max_score)

        def stats(values: list[float]) -> dict[str, float]:
            if not values:
                return {"min": 0.0, "mean": 0.0, "median": 0.0, "max": 0.0, "count": 0.0}
            ordered = sorted(values)
            mid = ordered[len(ordered) // 2]
            return {
                "min": ordered[0],
                "mean": statistics.mean(values),
                "median": mid,
                "max": ordered[-1],
                "count": float(len(values)),
            }

        return {key: stats(vals) for key, vals in groups.items()}

    def summary(self) -> dict[str, Any]:
        executed = [r for r in self.results if r.status in {"completed", "empty", "failed"}]
        hits = [r for r in executed if r.status == "completed" and r.hit]
        empty = [r for r in executed if r.status == "empty"]
        failed = [r for r in executed if r.status == "failed"]
        skipped = [r for r in self.results if r.status == "skipped"]
        expected_hits = [r for r in self.results if r.expect == "hit"]
        expected_empty = [r for r in self.results if r.expect == "empty"]
        hit_success = sum(1 for r in expected_hits if r.expect_success)
        empty_success = sum(1 for r in expected_empty if r.expect_success)
        top1 = sum(1 for r in self.results if r.source_match)
        topk = sum(1 for r in self.results if r.topk_source_match)
        lat = self.latencies or [0]
        sorted_lat = sorted(lat)
        p50 = sorted_lat[len(sorted_lat) // 2]
        p95 = sorted_lat[max(0, math.ceil(len(sorted_lat) * 0.95) - 1)]
        emb = [r.embedding_ms for r in self.results]
        vec = [r.vector_search_ms for r in self.results]
        post = [r.postprocess_ms for r in self.results]
        emb_sorted = sorted(emb or [0])
        vec_sorted = sorted(vec or [0])
        post_sorted = sorted(post or [0])
        executed_n = len(executed) or 1
        matrix = self.confusion_matrix()
        precision, recall = self.retrieval_precision_recall()
        mrr = self.mean_reciprocal_rank()
        return {
            "total_cases": self.total,
            "executed": len(executed),
            "skipped": len(skipped),
            "hits": len(hits),
            "empty": len(empty),
            "failed": len(failed),
            "hit_rate": len(hits) / executed_n,
            "empty_rate": len(empty) / executed_n,
            "failure_rate": len(failed) / executed_n,
            "expected_hit_count": len(expected_hits),
            "expected_empty_count": len(expected_empty),
            "correct_hit": matrix["tp"],
            "false_hit": matrix["fp"],
            "correct_empty": matrix["tn"],
            "false_empty": matrix["fn"],
            "precision": precision,
            "recall": recall,
            "expected_hit_success": hit_success,
            "expected_hit_total": len(expected_hits),
            "expected_empty_success": empty_success,
            "expected_empty_total": len(expected_empty),
            "top1_source_accuracy": top1 / (self.total or 1),
            "topk_source_accuracy": topk / (self.total or 1),
            "mrr": mrr,
            "effective_threshold": self.effective_threshold,
            "threshold_source": self.threshold_source,
            "avg_latency_ms": statistics.mean(lat),
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "embedding_avg_ms": statistics.mean(emb) if emb else 0.0,
            "embedding_p50_ms": emb_sorted[len(emb_sorted) // 2],
            "embedding_p95_ms": emb_sorted[max(0, math.ceil(len(emb_sorted) * 0.95) - 1)],
            "vector_search_avg_ms": statistics.mean(vec) if vec else 0.0,
            "vector_search_p50_ms": vec_sorted[len(vec_sorted) // 2],
            "vector_search_p95_ms": vec_sorted[max(0, math.ceil(len(vec_sorted) * 0.95) - 1)],
            "postprocess_avg_ms": statistics.mean(post) if post else 0.0,
            "postprocess_p50_ms": post_sorted[len(post_sorted) // 2],
            "postprocess_p95_ms": post_sorted[max(0, math.ceil(len(post_sorted) * 0.95) - 1)],
            "avg_max_score": statistics.mean([r.max_score for r in self.results]) if self.results else 0.0,
            "avg_context_chars": statistics.mean([r.context_chars for r in self.results]) if self.results else 0.0,
            "avg_retrieved_chars": statistics.mean([r.retrieved_chars for r in self.results]) if self.results else 0.0,
            "avg_selected_raw_chars": statistics.mean([r.selected_raw_chars for r in self.results]) if self.results else 0.0,
            "avg_selected_chars": statistics.mean([r.selected_chars for r in self.results]) if self.results else 0.0,
            "avg_formatted_context_chars": statistics.mean([r.formatted_context_chars for r in self.results]) if self.results else 0.0,
            "avg_chunks_discarded_by_budget": statistics.mean([r.chunks_discarded_by_budget for r in self.results]) if self.results else 0.0,
            "avg_candidate_count": statistics.mean([r.candidate_count for r in self.results]) if self.results else 0.0,
            "avg_selected_count": statistics.mean([r.selected_count for r in self.results]) if self.results else 0.0,
        }


def load_eval_dataset(path: Path) -> list[EvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases: list[EvalCase] = []
    for item in raw:
        cases.append(
            EvalCase(
                case_id=str(item["id"]),
                query=str(item["query"]),
                expect=str(item.get("expect", "hit")),
                expected_source_contains=[str(x).lower() for x in item.get("expected_source_contains", [])],
                security_expectation=str(item.get("security_expectation", "") or "").strip(),
                category=str(item.get("category", "") or "").strip(),
            )
        )
    return cases


def _source_blob(chunk) -> str:
    return f"{chunk.source_name} {chunk.source_reference} {chunk.text}".lower()


def _source_match(top_source: str, needles: list[str]) -> bool:
    if not needles:
        return True
    blob = (top_source or "").lower()
    return any(needle in blob for needle in needles)


def _topk_source_match(chunks, needles: list[str]) -> bool:
    if not needles:
        return True
    if not chunks:
        return False
    for chunk in chunks:
        blob = _source_blob(chunk)
        if any(needle in blob for needle in needles):
            return True
    return False


def _expected_rank(chunks, needles: list[str]) -> int:
    if not needles or not chunks:
        return 0
    for idx, chunk in enumerate(chunks, start=1):
        blob = _source_blob(chunk)
        if any(needle in blob for needle in needles):
            return idx
    return 0


def run_eval_for_tenant(
    *,
    tenant,
    cases: list[EvalCase],
    threshold: float | None = None,
    max_chunks: int | None = None,
    provider=None,
) -> EvalReport:
    cfg = load_embedding_config()
    if provider is None:
        provider = build_embedding_provider(cfg)
    results: list[EvalCaseResult] = []
    effective_threshold = 0.0
    threshold_source = "global_default"
    for case in cases:
        started = time.monotonic()
        kwargs: dict[str, Any] = {
            "tenant": tenant,
            "query": case.query,
            "provider": provider,
            "config": cfg,
        }
        if threshold is not None:
            kwargs["threshold_override"] = threshold
        if max_chunks is not None:
            kwargs["limit"] = max_chunks
        result = retrieve_context(**kwargs)
        effective_threshold = result.threshold
        threshold_source = result.threshold_source
        latency_ms = int((time.monotonic() - started) * 1000)
        top_source = ""
        if result.chunks:
            top = result.chunks[0]
            top_source = f"{top.source_name} {top.source_reference} {top.text}".strip()
        status = result.status
        hit = status == "completed" and bool(result.chunks)
        context_chars = len(result.context_text) if result.chunks else 0
        if case.expect == "hit":
            expect_success = hit and _topk_source_match(result.chunks, case.expected_source_contains)
        else:
            expect_success = status == "empty" or (status == "completed" and not hit)
        results.append(
            EvalCaseResult(
                case_id=case.case_id,
                expect=case.expect,
                status=status,
                hit=hit,
                top_source=top_source[:160],
                max_score=result.max_score,
                latency_ms=latency_ms,
                source_match=_source_match(top_source, case.expected_source_contains),
                topk_source_match=_topk_source_match(result.chunks, case.expected_source_contains),
                expect_success=expect_success,
                context_chars=context_chars,
                candidate_count=result.candidate_count,
                selected_count=len(result.chunks),
                expected_rank=_expected_rank(result.chunks, case.expected_source_contains),
                embedding_ms=result.embedding_ms,
                vector_search_ms=result.vector_search_ms,
                postprocess_ms=result.postprocess_ms,
                threshold=result.threshold,
                threshold_source=result.threshold_source,
                retrieved_chars=result.retrieved_chars,
                selected_raw_chars=result.selected_raw_chars,
                selected_chars=result.selected_chars,
                formatted_context_chars=result.formatted_context_chars,
                chunks_discarded_by_budget=result.chunks_discarded_by_budget,
            )
        )
    return EvalReport(
        tenant_slug=tenant.slug,
        total=len(cases),
        results=results,
        effective_threshold=effective_threshold,
        threshold_source=threshold_source,
    )
