from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from conversations.models import ChatRequest

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Livia-Request-ID"
REQUEST_ID_CONFLICT = "request_id_conflict"
REQUEST_IN_PROGRESS = "request_in_progress"
REQUEST_ID_REQUIRED = "request_id_required"
REQUEST_ID_INVALID = "request_id_invalid"
REQUEST_ID_HEADER_MISMATCH = "request_id_header_mismatch"
REQUEST_FAILED_RETRY = "request_failed_retry"


@dataclass(frozen=True)
class ChatRequestReservation:
    chat_request: ChatRequest | None
    state: str
    fingerprint: str = ""
    response_payload: dict | None = None
    response_status_code: int = 200
    error_code: str = ""
    replay: bool = False


class ChatIdempotencyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_request_id(payload_request_id, header_request_id: str = "") -> uuid.UUID:
    payload_value = str(payload_request_id or "").strip()
    header_value = str(header_request_id or "").strip()
    if not payload_value and not header_value:
        raise ChatIdempotencyError(REQUEST_ID_REQUIRED, "request_id is required.")
    if payload_value and header_value and payload_value != header_value:
        raise ChatIdempotencyError(REQUEST_ID_HEADER_MISMATCH, "request_id header mismatch.")
    raw_value = payload_value or header_value
    try:
        return uuid.UUID(raw_value)
    except (TypeError, ValueError) as exc:
        raise ChatIdempotencyError(REQUEST_ID_INVALID, "request_id must be a valid UUID.") from exc


def build_request_fingerprint(*, tenant_slug: str, session_id: str, request_id, message: str, source_page: str = "") -> str:
    normalized = {
        "tenant": str(tenant_slug or "").strip(),
        "session_id": str(session_id or "").strip(),
        "request_id": str(request_id),
        "message": str(message or "").strip(),
        "source_page": str(source_page or "").strip(),
    }
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reserve_chat_request(*, tenant, session_id: str, request_id, fingerprint: str) -> ChatRequestReservation:
    timeout_seconds = _processing_timeout_seconds()
    now = timezone.now()
    created = False
    try:
        with transaction.atomic():
            chat_request = ChatRequest.objects.create(
                tenant=tenant,
                session_id=session_id,
                request_id=request_id,
                request_fingerprint=fingerprint,
                status=ChatRequest.Status.PROCESSING,
            )
            created = True
    except IntegrityError:
        chat_request = _get_existing_request_locked(tenant=tenant, session_id=session_id, request_id=request_id)
        if chat_request is None:
            raise

    if created:
        _log_event("livia_chat_request_reserved", chat_request, code="reserved")
        return ChatRequestReservation(chat_request=chat_request, state="process", fingerprint=fingerprint)

    if chat_request.request_fingerprint != fingerprint:
        _log_event("livia_chat_request_conflict", chat_request, code=REQUEST_ID_CONFLICT)
        return ChatRequestReservation(
            chat_request=chat_request,
            state="conflict",
            fingerprint=fingerprint,
            error_code=REQUEST_ID_CONFLICT,
        )

    if chat_request.status == ChatRequest.Status.COMPLETED:
        _log_event("livia_chat_request_replay", chat_request, code="completed_replay")
        return ChatRequestReservation(
            chat_request=chat_request,
            state="replay",
            fingerprint=fingerprint,
            response_payload=dict(chat_request.response_payload or {}),
            response_status_code=chat_request.response_status_code,
            replay=True,
        )

    if chat_request.status == ChatRequest.Status.PROCESSING:
        if chat_request.updated_at and chat_request.updated_at <= now - timedelta(seconds=timeout_seconds):
            recovered = ChatRequest.objects.filter(
                pk=chat_request.pk,
                status=ChatRequest.Status.PROCESSING,
                updated_at=chat_request.updated_at,
            ).update(
                error_code="processing_abandoned_recovered",
                updated_at=timezone.now(),
            )
            if recovered:
                chat_request.refresh_from_db(fields=["error_code", "updated_at", "status"])
                _log_event("livia_chat_request_recovered", chat_request, code=chat_request.error_code)
                return ChatRequestReservation(chat_request=chat_request, state="process", fingerprint=fingerprint)
            chat_request.refresh_from_db(fields=["status", "updated_at"])
        _log_event("livia_chat_request_in_progress", chat_request, code=REQUEST_IN_PROGRESS)
        return ChatRequestReservation(
            chat_request=chat_request,
            state="in_progress",
            fingerprint=fingerprint,
            error_code=REQUEST_IN_PROGRESS,
        )

    if chat_request.status == ChatRequest.Status.FAILED:
        if chat_request.updated_at and chat_request.updated_at > now - timedelta(seconds=timeout_seconds):
            _log_event("livia_chat_request_failed_recent", chat_request, code=REQUEST_FAILED_RETRY)
            return ChatRequestReservation(
                chat_request=chat_request,
                state="failed",
                fingerprint=fingerprint,
                error_code=REQUEST_FAILED_RETRY,
            )
        chat_request.status = ChatRequest.Status.PROCESSING
        chat_request.response_payload = {}
        chat_request.response_status_code = 200
        chat_request.error_code = "failed_retry_recovered"
        chat_request.completed_at = None
        chat_request.save(update_fields=["status", "response_payload", "response_status_code", "error_code", "completed_at", "updated_at"])
        _log_event("livia_chat_request_recovered", chat_request, code=chat_request.error_code)
        return ChatRequestReservation(chat_request=chat_request, state="process", fingerprint=fingerprint)

    return ChatRequestReservation(chat_request=chat_request, state="failed", fingerprint=fingerprint, error_code="unknown_status")


def complete_chat_request(chat_request: ChatRequest, *, response_payload: dict, status_code: int = 200, conversation=None) -> ChatRequest:
    chat_request.status = ChatRequest.Status.COMPLETED
    if conversation is not None:
        chat_request.conversation = conversation
    chat_request.response_payload = dict(response_payload or {})
    chat_request.response_status_code = int(status_code or 200)
    chat_request.error_code = ""
    chat_request.completed_at = timezone.now()
    chat_request.save(update_fields=["status", "conversation", "response_payload", "response_status_code", "error_code", "completed_at", "updated_at"])
    _log_event("livia_chat_request_completed", chat_request, code="completed")
    return chat_request


def update_completed_chat_request_response(
    chat_request: ChatRequest,
    *,
    response_payload: dict,
    status_code: int = 200,
) -> ChatRequest:
    """Atualiza o payload reexecutável sem reabrir o estado do request."""
    if chat_request.status != ChatRequest.Status.COMPLETED:
        return chat_request
    chat_request.response_payload = dict(response_payload or {})
    chat_request.response_status_code = int(status_code or 200)
    chat_request.error_code = ""
    chat_request.save(update_fields=["response_payload", "response_status_code", "error_code", "updated_at"])
    return chat_request


def fail_chat_request(chat_request: ChatRequest | None, *, error_code: str) -> None:
    if chat_request is None:
        return
    try:
        chat_request.status = ChatRequest.Status.FAILED
        chat_request.error_code = str(error_code or "unexpected_error")[:80]
        chat_request.save(update_fields=["status", "error_code", "updated_at"])
        _log_event("livia_chat_request_failed", chat_request, code=chat_request.error_code)
    except Exception:
        logger.exception("livia_chat_request_failed_mark_error request_id=%s", getattr(chat_request, "request_id", ""))


def _get_existing_request_locked(*, tenant, session_id: str, request_id) -> ChatRequest | None:
    with transaction.atomic():
        queryset = ChatRequest.objects.filter(tenant=tenant, session_id=session_id, request_id=request_id)
        if connection.features.has_select_for_update:
            queryset = queryset.select_for_update()
        return queryset.first()


def _processing_timeout_seconds() -> int:
    value = int(getattr(settings, "LIVIA_CHAT_PROCESSING_TIMEOUT_SECONDS", 30) or 30)
    return max(value, 1)


def _log_event(event: str, chat_request: ChatRequest, *, code: str) -> None:
    started = chat_request.created_at or timezone.now()
    duration_ms = int(max((timezone.now() - started).total_seconds(), 0) * 1000)
    session_hash = hashlib.sha256(str(chat_request.session_id or "").encode("utf-8")).hexdigest()[:12]
    logger.info(
        "%s tenant_slug=%s session_hash=%s request_id=%s status=%s duration_ms=%s code=%s",
        event,
        chat_request.tenant.slug,
        session_hash,
        chat_request.request_id,
        chat_request.status,
        duration_ms,
        code,
    )
