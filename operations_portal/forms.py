from django import forms

from conversations.models import Conversation, HandoffRequest
from leads.models import LeadDraft
from tenants.models import AssistantProfile, Tenant

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

HANDOFF_STATUS_CHOICES = [
    (HandoffRequest.Status.PENDING, "Pendente"),
    (HandoffRequest.Status.SENT, "Notificado"),
    (HandoffRequest.Status.RESOLVED, "Resolvido"),
    (HandoffRequest.Status.CANCELLED, "Cancelado"),
]

HANDOFF_PRIORITY_CHOICES = [
    (HandoffRequest.Priority.LOW, "Baixa"),
    (HandoffRequest.Priority.NORMAL, "Normal"),
    (HandoffRequest.Priority.HIGH, "Alta"),
    (HandoffRequest.Priority.URGENT, "Urgente"),
]

HANDOFF_REASON_CHOICES = [
    (HandoffRequest.Reason.EXPLICIT_REQUEST, "Pedido explícito"),
    (HandoffRequest.Reason.QUALIFIED_LEAD, "Lead qualificado"),
    (HandoffRequest.Reason.TECHNICAL_COMPLEXITY, "Complexidade técnica"),
    (HandoffRequest.Reason.SUPPORT_REQUEST, "Suporte"),
    (HandoffRequest.Reason.EMERGENCY_OR_URGENT, "Emergência ou urgência"),
    (HandoffRequest.Reason.MANUAL, "Manual"),
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


class HandoffFilterForm(PortalFilterForm):
    tenant = forms.ModelChoiceField(queryset=Tenant.objects.order_by("name"), required=False, empty_label="Todos")
    status = forms.ChoiceField(required=False, choices=[("", "Todos")] + HANDOFF_STATUS_CHOICES)
    priority = forms.ChoiceField(required=False, choices=[("", "Todas")] + HANDOFF_PRIORITY_CHOICES)
    reason = forms.ChoiceField(required=False, choices=[("", "Todos")] + HANDOFF_REASON_CHOICES)
    start_date = forms.DateField(required=False, input_formats=["%Y-%m-%d"])
    end_date = forms.DateField(required=False, input_formats=["%Y-%m-%d"])
    q = forms.CharField(required=False, max_length=160)


class HumanHandoffSettingsForm(forms.ModelForm):
    class Meta:
        model = AssistantProfile
        fields = [
            "human_handoff_enabled",
            "human_handoff_channel",
            "handoff_whatsapp_number",
            "handoff_whatsapp_label",
            "handoff_whatsapp_message",
        ]
        widgets = {
            "handoff_whatsapp_message": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs["class"] = "form-control"
        self.fields["human_handoff_enabled"].label = "Ativar handoff humano"
        self.fields["human_handoff_channel"].label = "Canal"
        self.fields["handoff_whatsapp_number"].label = "Número do WhatsApp"
        self.fields["handoff_whatsapp_label"].label = "Texto do botão"
        self.fields["handoff_whatsapp_message"].label = "Mensagem pré-preenchida"
        self.fields["handoff_whatsapp_number"].help_text = "Use telefone internacional. O valor será salvo apenas com dígitos."
