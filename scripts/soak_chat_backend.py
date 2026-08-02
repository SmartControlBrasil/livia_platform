"""Transportes HTTP / Django test client para soak staging (Fases 17/19)."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

import requests
from django.test import Client


@dataclass(frozen=True)
class ChatCallResult:
    body: dict
    status: int
    latency_ms: int
    idempotent_replay: bool = False


class SoakChatBackend(Protocol):
    def post_chat(
        self,
        *,
        tenant: str,
        session_id: str,
        message: str,
        origin: str,
        request_id: str | None = None,
    ) -> ChatCallResult: ...


class DjangoTestChatBackend:
    def __init__(self, *, http_host: str = "localhost") -> None:
        self._client = Client(HTTP_HOST=http_host)

    def post_chat(
        self,
        *,
        tenant: str,
        session_id: str,
        message: str,
        origin: str,
        request_id: str | None = None,
    ) -> ChatCallResult:
        rid = request_id or str(uuid.uuid4())
        headers = {
            "HTTP_ORIGIN": origin,
            "HTTP_REFERER": origin + "/",
        }
        started = time.monotonic()
        resp = self._client.post(
            "/api/chat/",
            data=json.dumps(
                {
                    "tenant": tenant,
                    "session_id": session_id,
                    "request_id": rid,
                    "message": message,
                }
            ),
            content_type="application/json",
            **headers,
        )
        latency = int((time.monotonic() - started) * 1000)
        body = resp.json() if resp.status_code == 200 else {}
        replay = str(resp.headers.get("X-Livia-Idempotent-Replay", "")).lower() == "true"
        return ChatCallResult(body=body, status=resp.status_code, latency_ms=latency, idempotent_replay=replay)


class HttpSoakChatBackend:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 120.0,
        verify_tls: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds
        self.verify_tls = verify_tls
        self._session = requests.Session()

    def post_chat(
        self,
        *,
        tenant: str,
        session_id: str,
        message: str,
        origin: str,
        request_id: str | None = None,
    ) -> ChatCallResult:
        rid = request_id or str(uuid.uuid4())
        url = urljoin(self.base_url, "api/chat/")
        headers = {
            "Origin": origin,
            "Referer": origin + "/",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Livia-Tenant": tenant,
        }
        payload = {
            "tenant": tenant,
            "session_id": session_id,
            "request_id": rid,
            "message": message,
        }
        started = time.monotonic()
        resp = self._session.post(
            url,
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
            verify=self.verify_tls,
        )
        latency = int((time.monotonic() - started) * 1000)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        replay = str(resp.headers.get("X-Livia-Idempotent-Replay", "")).lower() == "true"
        return ChatCallResult(body=body, status=resp.status_code, latency_ms=latency, idempotent_replay=replay)
