from __future__ import annotations

import json
import uuid
from contextlib import ExitStack

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import Client, override_settings
from django.utils import timezone
from unittest.mock import patch

from audit.models import AuditEvent
from conversations.models import ChatRequest, Conversation, HandoffRequest, Message
from integrations.models import OutboxEvent, WebhookDeliveryLog
from leads.models import LeadDraft
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Smoke local de chat por tenant com rollback por padrão e bloqueio de side effects externos."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--scenario", default="commercial")
        parser.add_argument("--persist", action="store_true")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"] or "").strip()
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError(f"Tenant não encontrado: {tenant_slug}")
        if str(options["scenario"] or "").strip() != "commercial":
            raise CommandError("Somente --scenario=commercial está disponível nesta fase.")

        with transaction.atomic():
            before = self._snapshot(tenant)
            report = self._run_commercial_smoke(tenant=tenant)
            after = self._snapshot(tenant)
            persisted = bool(options["persist"])
            if not persisted:
                transaction.set_rollback(True)

        payload = {
            "tenant": tenant.slug,
            "scenario": "commercial",
            "mode": "DETERMINISTIC_ONLY",
            "persisted": persisted,
            "rollback_applied": not persisted,
            "before": before,
            "after": after,
            "checks": report["checks"],
            "external_calls": report["external_calls"],
            "timestamp": timezone.now().isoformat(),
        }
        if options["json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(f"Tenant: {tenant.slug}")
            self.stdout.write("Scenario: commercial")
            self.stdout.write("Mode: DETERMINISTIC_ONLY")
            self.stdout.write(f"Rollback applied: {str(not persisted).lower()}")
            self.stdout.write("")
            for item in report["checks"]:
                self.stdout.write(f"- {item['code']}: {item['status']}")
            self.stdout.write("")
            self.stdout.write("DB snapshot (during run):")
            for key, value in after.items():
                self.stdout.write(f"  {key}: {value['count']}")
            self.stdout.write("")
            self.stdout.write(f"External call attempts blocked: {report['external_calls']}")

    def _run_commercial_smoke(self, *, tenant: Tenant) -> dict:
        checks: list[dict] = []
        external_calls = {"smart360_http": 0, "webhook_http": 0, "openai_chat": 0, "openai_embedding": 0}

        def _blocked_call(key: str):
            def _raise(*args, **kwargs):
                external_calls[key] += 1
                raise AssertionError(f"external_call_blocked:{key}")

            return _raise

        client = Client()
        base_headers = {
            "HTTP_ORIGIN": "https://www.granimarmorespitondo.com.br",
            "HTTP_X_LIVIA_TENANT": tenant.slug,
            "content_type": "application/json",
        }

        with ExitStack() as stack:
            stack.enter_context(
                override_settings(
                    ALLOWED_HOSTS=["127.0.0.1", "localhost", "testserver"],
                    LIVIA_AI_ENABLED=False,
                    LIVIA_AI_DRY_RUN=True,
                    LIVIA_RAG_ENABLED=False,
                    LIVIA_CHAT_RATE_LIMIT_ENABLED=False,
                    SMART360_LEAD_DISPATCH_ENABLED=True,
                    SMART360_LEAD_DISPATCH_DRY_RUN=True,
                    SMART360_LEAD_DISPATCH_REAL_ENABLED=False,
                    LIVIA_WEBHOOKS_ENABLED=False,
                    LIVIA_WEBHOOKS_DRY_RUN=True,
                    LIVIA_WEBHOOKS_REAL_ENABLED=False,
                )
            )
            stack.enter_context(patch("integrations.smart360.client.requests.post", side_effect=_blocked_call("smart360_http")))
            stack.enter_context(patch("integrations.webhooks.service.requests.post", side_effect=_blocked_call("webhook_http")))
            stack.enter_context(
                patch("integrations.openai.client.OpenAIChatClient.create_chat_completion", side_effect=_blocked_call("openai_chat"))
            )
            stack.enter_context(
                patch("knowledge_base.rag.embeddings.OpenAIEmbeddingProvider.embed_texts", side_effect=_blocked_call("openai_embedding"))
            )

            session_id = f"smoke-{uuid.uuid4().hex[:12]}"

            def post(message: str, request_id: str | None = None):
                req_id = request_id or str(uuid.uuid4())
                payload = {
                    "tenant": tenant.slug,
                    "session_id": session_id,
                    "request_id": req_id,
                    "message": message,
                }
                response = client.post(
                    "/api/chat/",
                    data=json.dumps(payload),
                    HTTP_X_LIVIA_REQUEST_ID=req_id,
                    **base_headers,
                )
                return response, req_id

            # 1 saudação
            response_1, req_1 = post("Olá, gostaria de fazer um orçamento.")
            self._assert_status(response_1, "scenario_1_greeting", checks)

            # 2 tipo de projeto
            response_2, _req_2 = post("É uma bancada para cozinha.")
            self._assert_status(response_2, "scenario_2_project_type", checks)

            # 3 localização
            response_3, _req_3 = post("A obra fica no Jardim da Saúde, em São Paulo.")
            self._assert_status(response_3, "scenario_3_location", checks)

            # 4 medidas desconhecidas
            response_4, _req_4 = post("Ainda não tenho as medidas.")
            self._assert_status(response_4, "scenario_4_unknown_measurements", checks)

            # 5 contato
            response_5, _req_5 = post("Meu nome é Marcelo e meu WhatsApp é 11940241328.")
            self._assert_status(response_5, "scenario_5_contact", checks)
            checks.append(
                {
                    "code": "lead_draft_created_or_updated",
                    "status": "PASS" if LeadDraft.objects.filter(tenant=tenant).count() <= 1 else "FAIL",
                }
            )

            # 6 retry idempotente
            response_6a, retry_request_id = post("Pode repetir por favor?", request_id=str(uuid.uuid4()))
            response_6b, _ = post("Pode repetir por favor?", request_id=retry_request_id)
            self._assert_status(response_6a, "scenario_6_retry_first", checks)
            self._assert_status(response_6b, "scenario_6_retry_replay", checks)
            checks.append(
                {
                    "code": "retry_idempotency_chat_request_unique",
                    "status": "PASS"
                    if ChatRequest.objects.filter(tenant=tenant, session_id=session_id, request_id=retry_request_id).count() == 1
                    else "FAIL",
                }
            )

            # 7 handoff
            response_7, _req_7 = post("Quero falar com um atendente.")
            self._assert_status(response_7, "scenario_7_handoff", checks)
            checks.append(
                {
                    "code": "handoff_created_no_duplicate",
                    "status": "PASS" if HandoffRequest.objects.filter(tenant=tenant, conversation__session_id=session_id).count() <= 1 else "FAIL",
                }
            )

            # 8 preço
            response_8, _req_8 = post("Quanto custa uma bancada de granito?")
            self._assert_status(response_8, "scenario_8_price", checks)
            response_8_data = self._safe_json(response_8)
            checks.append(
                {
                    "code": "price_not_invented",
                    "status": "PASS" if "r$" not in str(response_8_data.get("reply", "")).lower() else "FAIL",
                }
            )

            # 9 prazo
            response_9, _req_9 = post("Vocês conseguem instalar na semana que vem?")
            self._assert_status(response_9, "scenario_9_deadline", checks)

            # 10 material não documentado
            response_10, _req_10 = post("Vocês trabalham com Dekton?")
            self._assert_status(response_10, "scenario_10_unknown_material", checks)

            checks.append(
                {
                    "code": "conversation_persisted",
                    "status": "PASS" if Conversation.objects.filter(tenant=tenant, session_id=session_id).exists() else "FAIL",
                }
            )
            checks.append(
                {
                    "code": "messages_persisted",
                    "status": "PASS"
                    if Message.objects.filter(conversation__tenant=tenant, conversation__session_id=session_id).count() >= 2
                    else "FAIL",
                }
            )
            checks.append(
                {
                    "code": "no_external_calls",
                    "status": "PASS" if sum(external_calls.values()) == 0 else "FAIL",
                }
            )

        failed = [item["code"] for item in checks if item["status"] != "PASS"]
        if failed:
            raise CommandError(f"Smoke comercial falhou: {', '.join(failed)}")
        return {"checks": checks, "external_calls": external_calls}

    def _assert_status(self, response, code: str, checks: list[dict]):
        checks.append({"code": code, "status": "PASS" if response.status_code == 200 else "FAIL"})

    def _safe_json(self, response) -> dict:
        try:
            return response.json()
        except Exception:
            return {}

    def _snapshot(self, tenant: Tenant) -> dict:
        conversations = Conversation.objects.filter(tenant=tenant).order_by("-id")
        leads = LeadDraft.objects.filter(tenant=tenant).order_by("-id")
        handoffs = HandoffRequest.objects.filter(tenant=tenant).order_by("-id")
        outbox = OutboxEvent.objects.filter(tenant=tenant).order_by("-id")
        webhook_logs = WebhookDeliveryLog.objects.filter(tenant=tenant).order_by("-id")
        chat_requests = ChatRequest.objects.filter(tenant=tenant).order_by("-id")
        audits = AuditEvent.objects.filter(tenant=tenant).order_by("-id")

        return {
            "Conversation": {"count": conversations.count(), "ids": list(conversations.values_list("id", flat=True)[:5])},
            "Message": {
                "count": Message.objects.filter(conversation__tenant=tenant).count(),
                "ids": list(
                    Message.objects.filter(conversation__tenant=tenant).order_by("-id").values_list("id", flat=True)[:5]
                ),
            },
            "ChatRequest": {
                "count": chat_requests.count(),
                "ids": list(chat_requests.values_list("id", flat=True)[:5]),
                "statuses": list(chat_requests.values_list("status", flat=True)[:5]),
            },
            "LeadDraft": {"count": leads.count(), "ids": list(leads.values_list("id", flat=True)[:5]), "statuses": list(leads.values_list("status", flat=True)[:5])},
            "HandoffRequest": {
                "count": handoffs.count(),
                "ids": list(handoffs.values_list("id", flat=True)[:5]),
                "statuses": list(handoffs.values_list("status", flat=True)[:5]),
            },
            "OutboxEvent": {
                "count": outbox.count(),
                "ids": list(outbox.values_list("id", flat=True)[:5]),
                "statuses": list(outbox.values_list("status", flat=True)[:5]),
            },
            "WebhookDeliveryLog": {
                "count": webhook_logs.count(),
                "ids": list(webhook_logs.values_list("id", flat=True)[:5]),
                "statuses": list(webhook_logs.values_list("status", flat=True)[:5]),
            },
            "AuditEvent": {"count": audits.count(), "ids": list(audits.values_list("id", flat=True)[:5])},
        }
