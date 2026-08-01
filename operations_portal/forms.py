from django import forms

from conversations.models import Conversation, HandoffRequest
from knowledge_base.models import TenantRagDriveFileManifest
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
        tenant_queryset = kwargs.pop("tenant_queryset", None)
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None and "tenant" in self.fields:
            self.fields["tenant"].queryset = tenant_queryset
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


MANIFEST_STATUS_CHOICES = [
    ("discovered", "Descoberto"),
    ("exported", "Exportado"),
    ("updated", "Atualizado"),
    ("unchanged", "Sem alteração"),
    ("skipped_unsupported", "Ignorado (não suportado)"),
    ("failed", "Falha"),
    ("removed", "Removido"),
    ("unavailable", "Indisponível"),
]

CHUNK_STATUS_CHOICES = [
    ("active", "Ativo"),
    ("replaced", "Substituído"),
    ("failed", "Falha"),
]


class KnowledgeDocumentFilterForm(PortalFilterForm):
    status = forms.ChoiceField(required=False, choices=[("", "Todos")] + MANIFEST_STATUS_CHOICES)
    is_active = forms.ChoiceField(required=False, choices=[("", "Todos"), ("yes", "Ativo"), ("no", "Inativo")])
    q = forms.CharField(required=False, max_length=120)


class KnowledgeChunkFilterForm(PortalFilterForm):
    status = forms.ChoiceField(required=False, choices=[("", "Todos")] + CHUNK_STATUS_CHOICES)
    is_active = forms.ChoiceField(required=False, choices=[("", "Todos"), ("yes", "Ativo"), ("no", "Inativo")])
    has_embedding = forms.ChoiceField(
        required=False,
        choices=[("", "Todos"), ("yes", "Com embedding"), ("no", "Sem embedding")],
    )
    manifest = forms.ModelChoiceField(queryset=TenantRagDriveFileManifest.objects.none(), required=False, empty_label="Todos")

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant is not None:
            self.fields["manifest"].queryset = TenantRagDriveFileManifest.objects.filter(tenant=tenant).order_by("name")


class KnowledgeDiagnosticSearchForm(forms.Form):
    query = forms.CharField(
        required=True,
        max_length=500,
        label="Consulta diagnóstica",
        widget=forms.TextInput(attrs={"placeholder": "Ex.: mármore Carrara para bancada"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["query"].widget.attrs["class"] = "form-control"


class TenantRagConfigurationPortalForm(forms.ModelForm):
    class Meta:
        from knowledge_base.models import TenantRagConfiguration

        model = TenantRagConfiguration
        fields = [
            "retrieval_enabled",
            "min_similarity_score",
            "max_retrieved_chunks",
            "max_context_chars",
            "retrieval_timeout_seconds",
        ]

    def __init__(self, *args, global_limits=None, **kwargs):
        self.global_limits = global_limits
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"
        self.fields["retrieval_enabled"].label = "Habilitar recuperação RAG no chat"
        self.fields["min_similarity_score"].label = "Score mínimo (override)"
        self.fields["min_similarity_score"].required = False
        self.fields["max_retrieved_chunks"].label = "Máximo de chunks (override)"
        self.fields["max_retrieved_chunks"].required = False
        self.fields["max_context_chars"].label = "Orçamento de contexto em caracteres (override)"
        self.fields["max_context_chars"].required = False
        self.fields["retrieval_timeout_seconds"].label = "Timeout da recuperação em segundos (override)"
        self.fields["retrieval_timeout_seconds"].required = False
        limits = global_limits
        if limits is not None:
            self.fields["max_retrieved_chunks"].help_text = (
                f"Deixe vazio para herdar o limite global ({limits.global_max_chunks}). "
                "O tenant só pode restringir esse teto."
            )
            self.fields["max_context_chars"].help_text = (
                f"Deixe vazio para herdar o limite global ({limits.global_max_context_chars} caracteres). "
                "O tenant só pode restringir esse teto."
            )
            self.fields["retrieval_timeout_seconds"].help_text = (
                f"Deixe vazio para herdar o timeout global ({limits.global_timeout_seconds}s). "
                "O tenant só pode reduzir o timeout efetivo."
            )

    def clean(self):
        cleaned = super().clean()
        limits = self.global_limits
        if limits is None:
            return cleaned
        max_chunks = cleaned.get("max_retrieved_chunks")
        if max_chunks is not None and int(max_chunks) > limits.global_max_chunks:
            self.add_error(
                "max_retrieved_chunks",
                f"O valor não pode exceder o limite global de {limits.global_max_chunks}.",
            )
        max_chars = cleaned.get("max_context_chars")
        if max_chars is not None and int(max_chars) > limits.global_max_context_chars:
            self.add_error(
                "max_context_chars",
                f"O valor não pode exceder o limite global de {limits.global_max_context_chars} caracteres.",
            )
        timeout = cleaned.get("retrieval_timeout_seconds")
        if timeout is not None and int(timeout) > limits.global_timeout_seconds:
            self.add_error(
                "retrieval_timeout_seconds",
                f"O valor não pode exceder o timeout global de {limits.global_timeout_seconds}s.",
            )
        return cleaned


PORTAL_RAG_OPERATIONS = [
    ("inventory", "Inventário da origem"),
    ("sync_export", "Sincronização de documentos"),
    ("build_chunks", "Atualização de chunks"),
    ("index_embeddings", "Geração de embeddings pendentes"),
    ("full_reindex", "Reindexação completa"),
]


class KnowledgeBaseOperationRequestForm(forms.Form):
    operation = forms.ChoiceField(choices=PORTAL_RAG_OPERATIONS, label="Operação")
    confirm_reindex = forms.BooleanField(required=False, label="Confirmo o impacto da reindexação completa")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["operation"].widget.attrs["class"] = "form-select"
        self.fields["confirm_reindex"].widget.attrs["class"] = "form-check-input"
