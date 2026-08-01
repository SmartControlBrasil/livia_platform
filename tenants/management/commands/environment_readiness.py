from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from config.environment_safety import EnvironmentCheck, inspect_environment_safety, summarize_environment_readiness
from knowledge_base.rag.embeddings import EmbeddingConfigurationError, load_embedding_config
from knowledge_base.rag.readiness import inspect_rag_vector_readiness


class Command(BaseCommand):
    help = "Readiness operacional de ambiente (config safety + RAG profile)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", default="", help="Slug opcional para validar gates GP.")
        parser.add_argument("--json", action="store_true", help="Emitir JSON.")

    def handle(self, *args, **options):
        tenant_slug = str(options.get("tenant") or "").strip() or None
        checks = inspect_environment_safety(tenant_slug=tenant_slug)
        status = summarize_environment_readiness(checks)

        embedding_error = ""
        try:
            profile = load_embedding_config()
            embedding_detail = f"{profile.provider}/{profile.model}/dim={profile.dimension}"
        except EmbeddingConfigurationError as exc:
            embedding_error = str(exc)
            embedding_detail = "invalid"
            checks = list(checks) + [
                EnvironmentCheck(
                    ok=False,
                    code="embedding_config",
                    detail=embedding_error,
                    level="critical",
                )
            ]
            status = summarize_environment_readiness(checks)

        rag_checks = []
        if connection.vendor == "postgresql":
            rag_checks = [
                {"ok": item.ok, "code": item.code, "detail": item.detail}
                for item in inspect_rag_vector_readiness()
            ]
            if any(not item["ok"] for item in rag_checks):
                status = "NOT_READY" if status == "READY" else status

        payload = {
            "status": status,
            "environment": getattr(__import__("django.conf", fromlist=["settings"]).settings, "LIVIA_ENVIRONMENT", ""),
            "checks": [
                {"ok": item.ok, "code": item.code, "detail": item.detail, "level": item.level}
                for item in checks
            ],
            "embedding_profile": embedding_detail if not embedding_error else None,
            "embedding_error": embedding_error or None,
            "rag_vector_readiness": rag_checks,
        }

        if options["json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Environment readiness: {status}")
            self.stdout.write(f"LIVIA_ENVIRONMENT={payload['environment']}")
            if embedding_error:
                self.stdout.write(self.style.ERROR(f"embedding_config: {embedding_error}"))
            else:
                self.stdout.write(f"embedding_profile: {embedding_detail}")
            for item in checks:
                mark = "OK" if item.ok else item.level.upper()
                style = self.style.SUCCESS if item.ok else self.style.ERROR
                self.stdout.write(style(f"[{mark}] {item.code}: {item.detail}"))
            if rag_checks:
                self.stdout.write("")
                self.stdout.write("RAG vector readiness:")
                for item in rag_checks:
                    mark = "OK" if item["ok"] else "FAIL"
                    line = f"[{mark}] {item['code']}: {item['detail']}"
                    self.stdout.write(self.style.SUCCESS(line) if item["ok"] else self.style.ERROR(line))

        if status == "NOT_READY":
            raise SystemExit(1)
