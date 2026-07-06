"""Serviços de leads da Lívia."""

from .crm_dispatch import CRMDispatchResult, CRMDispatchService
from .lead_capture import LeadCaptureResult, LeadCaptureService

__all__ = [
    "CRMDispatchResult",
    "CRMDispatchService",
    "LeadCaptureResult",
    "LeadCaptureService",
]
