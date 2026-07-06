from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .contracts import LeadIngestPayload, LeadIngestResponse


class Smart360GrowthClient:
    def __init__(self, base_url: str = "", token: str = "", dry_run: bool = True):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.dry_run = dry_run

    def ingest_lead(self, payload: LeadIngestPayload | dict[str, Any]) -> LeadIngestResponse:
        normalized_payload = self._normalize_payload(payload)
        if self.dry_run:
            return LeadIngestResponse(
                success=True,
                dry_run=True,
                message="dry_run ativo: lead não foi enviado ao Smart360.",
                status_code=202,
                external_id=None,
                data={
                    "endpoint": self._lead_ingest_url(),
                    "payload": normalized_payload,
                },
            )

        return self._post_lead(normalized_payload)

    def _normalize_payload(self, payload: LeadIngestPayload | dict[str, Any]) -> dict[str, Any]:
        if isinstance(payload, LeadIngestPayload):
            return asdict(payload)
        return dict(payload)

    def _lead_ingest_url(self) -> str:
        if not self.base_url:
            return ""
        return f"{self.base_url}/api/smart360/leads/ingest/"

    def _post_lead(self, payload: dict[str, Any]) -> LeadIngestResponse:
        if not self.base_url:
            raise ValueError("base_url é obrigatório quando dry_run=False.")

        try:
            import requests
        except ImportError as exc:  # pragma: no cover - caminho futuro
            raise RuntimeError("requests não está instalado.") from exc

        headers = {
            "Authorization": f"Bearer {self.token}" if self.token else "",
            "Content-Type": "application/json",
        }
        headers = {key: value for key, value in headers.items() if value}

        response = requests.post(self._lead_ingest_url(), json=payload, headers=headers, timeout=15)
        try:
            response_data = response.json()
        except ValueError:
            response_data = {"detail": response.text}

        return LeadIngestResponse(
            success=response.ok,
            dry_run=False,
            message=str(response_data.get("message") or response_data.get("detail") or "ok"),
            status_code=response.status_code,
            external_id=response_data.get("external_id") or response_data.get("id"),
            data=response_data,
        )
