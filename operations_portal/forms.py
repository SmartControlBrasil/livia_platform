from django import forms

from conversations.models import Conversation
from leads.models import LeadDraft
from tenants.models import Tenant

CONVERSATION_LEAD_STATE_CHOICES = [
    (Conversation.LeadState.DISCOVERY, "Descoberta"),
    (Conversation.LeadState.OFFER_HANDOFF, "Oferta de atendimento humano"),
    (Conversation.LeadState.COLLECT_NEED, "Coleta da necessidade"),
    (Conversation.LeadState.COLLECT_NAME_COMPANY, "Nome e empresa"),
    (Conversation.LeadState.COLLECT_CONTACT, "Contato"),
    (Conversation.LeadState.QUALIFIED, "Qualificada"),
    (Conversation.LeadState.CLOSED, "Encerrada"),
]

LEAD_STATUS_CHOICES = [
    (LeadDraft.Status.DRAFT, "Rascunho"),
    (LeadDraft.Status.QUALIFIED, "Qualificado"),
    (LeadDraft.Status.SENT_TO_CRM, "Enviado ao CRM"),
    (LeadDraft.Status.FAILED, "Falha"),
]


class PortalFilterForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            css_class = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs["class"] = css_class
            if isinstance(field, forms.DateField):
                field.widget.input_type = "date"
            if name == "q":
                field.widget.attrs.setdefault("placeholder", "Buscar")


class ConversationFilterForm(PortalFilterForm):
    tenant = forms.ModelChoiceField(queryset=Tenant.objects.order_by("name"), required=False, empty_label="Todos")
    lead_state = forms.ChoiceField(required=False, choices=[("", "Todos")] + CONVERSATION_LEAD_STATE_CHOICES)
    qualified = forms.ChoiceField(required=False, choices=[("", "Todos"), ("yes", "Sim"), ("no", "Não")])
    start_date = forms.DateField(required=False, input_formats=["%Y-%m-%d"])
    end_date = forms.DateField(required=False, input_formats=["%Y-%m-%d"])
    q = forms.CharField(required=False, max_length=120)


class LeadFilterForm(PortalFilterForm):
    tenant = forms.ModelChoiceField(queryset=Tenant.objects.order_by("name"), required=False, empty_label="Todos")
    status = forms.ChoiceField(required=False, choices=[("", "Todos")] + LEAD_STATUS_CHOICES)
    crm_sent = forms.ChoiceField(required=False, choices=[("", "Todos"), ("yes", "Sim"), ("no", "Não")])
    dispatch_failed = forms.ChoiceField(required=False, choices=[("", "Todos"), ("yes", "Sim"), ("no", "Não")])
    start_date = forms.DateField(required=False, input_formats=["%Y-%m-%d"])
    end_date = forms.DateField(required=False, input_formats=["%Y-%m-%d"])
    q = forms.CharField(required=False, max_length=160)
