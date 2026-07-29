from integrations.models import OutboxEvent

from .handlers import ConversationSummaryReadyHandler, HandoffCreatedHandler, LeadQualifiedHandler

HANDLERS = {
    OutboxEvent.EventType.LEAD_QUALIFIED: LeadQualifiedHandler,
    OutboxEvent.EventType.HANDOFF_CREATED: HandoffCreatedHandler,
    OutboxEvent.EventType.CONVERSATION_SUMMARY_READY: ConversationSummaryReadyHandler,
}


def get_handler(event_type: str):
    handler_cls = HANDLERS.get(event_type)
    if handler_cls is None:
        raise KeyError(f"No outbox handler registered for {event_type}.")
    return handler_cls()
