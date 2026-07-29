from __future__ import annotations

from dataclasses import asdict
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - fallback leve para ambientes sem requests
    class _RequestsUnavailableError(Exception):
        pass

    class _RequestsShim:
        RequestException = _RequestsUnavailableError

        @staticmethod
        def post(*args, **kwargs):
            raise _RequestsUnavailableError("requests não está instalado.")

    requests = _RequestsShim()

from .contracts import LeadIngestPayload, LeadIngestResponse


class Smart360GrowthClient:
    def __init__(self, base_url: str = "", token: str = "", dry_run: bool = True):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.dry_run = dry_run

    def ingest_lead(self, payload: LeadIngestPayload | dict[str, Any], *, idempotency_key: str = "") -> LeadIngestResponse:
        normalized_payload = self._normalize_payload(payload)
        if self.dry_run:
            tenant_slug = str(normalized_payload.get("tenant_slug") or "tenant")
            conversation_id = str(normalized_payload.get("conversation_id") or "lead")
            return LeadIngestResponse(
                success=True,
                dry_run=True,
                message="dry_run ativo: lead não foi enviado ao Smart360.",
                status_code=202,
                external_id=f"dry-run-{tenant_slug}-{conversation_id}",
                data={
                    "endpoint": self._lead_ingest_url(),
                    "payload": normalized_payload,
                },
            )

        return self._post_lead(normalized_payload, idempotency_key=idempotency_key)

    def _normalize_payload(self, payload: LeadIngestPayload | dict[str, Any]) -> dict[str, Any]:
        if isinstance(payload, LeadIngestPayload):
            return asdict(payload)
        return dict(payload)

    def _lead_ingest_url(self) -> str:
        if not self.base_url:
            return ""
        return f"{self.base_url}/api/v1/growth/leads/ingest/"

    def _post_lead(self, payload: dict[str, Any], *, idempotency_key: str = "") -> LeadIngestResponse:
        if not self.base_url:
            raise ValueError("base_url é obrigatório quando dry_run=False.")

        headers = {
            "Authorization": f"Bearer {self.token}" if self.token else "",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Livia-Event-ID"] = idempotency_key
            headers["Idempotency-Key"] = idempotency_key
        headers = {key: value for key, value in headers.items() if value}

        try:
            response = requests.post(self._lead_ingest_url(), json=payload, headers=headers, timeout=10)
            try:
                response_data = response.json()
            except ValueError:
                return LeadIngestResponse(
                    success=False,
                    dry_run=False,
                    message="Resposta JSON inválida do Smart360.",
                    status_code=response.status_code,
                    data={"detail": response.text},
                )

            success = response.ok and isinstance(response_data, dict)
            return LeadIngestResponse(
                success=success,
                dry_run=False,
                message=str(response_data.get("message") or response_data.get("detail") or "ok"),
                status_code=response.status_code,
                external_id=response_data.get("external_id") or response_data.get("id"),
                data=response_data,
            )
        except requests.RequestException as exc:
            return LeadIngestResponse(
                success=False,
                dry_run=False,
                message=f"Falha ao enviar lead para o Smart360: {exc.__class__.__name__}.",
                status_code=503,
                data={"detail": "request_error"},
            )
