from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class LeadIngestPayload:
    tenant_slug: str
    name: str = ""
    company: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    need_summary: str = ""
    source_page: str = ""
    conversation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LeadIngestResponse:
    success: bool
    dry_run: bool
    message: str
    status_code: int = 200
    external_id: str | None = None
    data: dict[str, Any] | None = None
