from __future__ import annotations

import ipaddress
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db import models
from django.forms.models import model_to_dict
from django.utils import timezone

from .models import AuditEvent

MASKED_VALUE = "[masked]"
SERIALIZATION_ERROR_VALUE = "[unserializable]"
MAX_TEXT_LENGTH = 500
SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "transcript",
)


def record_audit_event(
    *,
    action,
    actor=None,
    tenant=None,
    obj=None,
    object_type="",
    object_id="",
    object_repr="",
    before_data=None,
    after_data=None,
    metadata=None,
    request=None,
):
    actor = _normalize_actor(actor)
    if tenant is None and obj is not None:
        tenant = getattr(obj, "tenant", None)
    if obj is not None:
        object_type = object_type or _object_type(obj)
        object_id = object_id or str(getattr(obj, "pk", "") or "")
        object_repr = object_repr or str(obj)

    return AuditEvent.objects.create(
        tenant=tenant,
        actor=actor,
        action=action,
        object_type=_truncate_text(object_type, 120),
        object_id=_truncate_text(object_id, 120),
        object_repr=_truncate_text(object_repr, 220),
        before_data=sanitize_audit_data(before_data or {}),
        after_data=sanitize_audit_data(after_data or {}),
        metadata=sanitize_audit_data(metadata or {}),
        ip_address=extract_ip_address(request),
    )


def audit_model_snapshot(obj, fields=None):
    if obj is None:
        return {}
    if fields is not None and not fields:
        return {}
    field_names = list(fields) if fields is not None else [
        field.name
        for field in obj._meta.fields
        if not isinstance(field, (models.AutoField, models.BigAutoField))
    ]
    data = model_to_dict(obj, fields=field_names)
    for field_name in field_names:
        field = obj._meta.get_field(field_name)
        if field.many_to_one or field.one_to_one:
            value = getattr(obj, f"{field_name}_id", None)
            data[field_name] = value
    return sanitize_audit_data(data)


def changed_fields(before, after):
    before = sanitize_audit_data(before or {})
    after = sanitize_audit_data(after or {})
    keys = sorted(set(before) | set(after))
    return {
        "before": {key: before.get(key) for key in keys if before.get(key) != after.get(key)},
        "after": {key: after.get(key) for key in keys if before.get(key) != after.get(key)},
    }


def sanitize_audit_data(value):
    try:
        return _sanitize(value)
    except Exception:
        return SERIALIZATION_ERROR_VALUE


def extract_ip_address(request):
    if request is None:
        return None

    candidates = []
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    candidates.extend(part.strip() for part in forwarded_for.split(",") if part.strip())
    remote_addr = request.META.get("REMOTE_ADDR", "")
    if remote_addr:
        candidates.append(remote_addr.strip())

    for candidate in candidates:
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return None


def _sanitize(value):
    if isinstance(value, dict):
        return {
            str(key): MASKED_VALUE if _is_sensitive_key(key) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    if isinstance(value, models.Model):
        return str(getattr(value, "pk", "") or value)
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return _truncate_text(value, MAX_TEXT_LENGTH)
    if value is None or isinstance(value, (bool, int, float)):
        return value

    try:
        json.dumps(value)
        return value
    except TypeError:
        return SERIALIZATION_ERROR_VALUE


def _normalize_actor(actor):
    if actor is not None and getattr(actor, "is_authenticated", True):
        return actor
    return None


def _is_sensitive_key(key):
    lowered = str(key).lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def _truncate_text(value, limit):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 15]}...[truncated]"


def _object_type(obj):
    return f"{obj._meta.app_label}.{obj._meta.model_name}"
