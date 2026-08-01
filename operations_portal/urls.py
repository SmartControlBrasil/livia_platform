from django.urls import path

from . import knowledge_base_views, views

app_name = "operations_portal"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("conversas/", views.conversation_list, name="conversation_list"),
    path("conversas/<int:pk>/", views.conversation_detail, name="conversation_detail"),
    path("leads/", views.lead_list, name="lead_list"),
    path("handoffs/", views.handoff_list, name="handoff_list"),
    path("handoffs/<int:pk>/", views.handoff_detail, name="handoff_detail"),
    path("handoffs/<int:pk>/status/", views.update_handoff_status, name="handoff_update_status"),
    path("configuracoes/", views.settings_view, name="settings"),
    path("leads/<int:pk>/", views.lead_detail, name="lead_detail"),
    path("leads/<int:pk>/reprocessar-crm/", views.retry_lead_crm_dispatch, name="lead_retry_crm"),
    path("base-de-conhecimento/", knowledge_base_views.knowledge_base_dashboard, name="knowledge_base_dashboard"),
    path("base-de-conhecimento/documentos/", knowledge_base_views.knowledge_base_documents, name="knowledge_base_documents"),
    path("base-de-conhecimento/chunks/", knowledge_base_views.knowledge_base_chunks, name="knowledge_base_chunks"),
    path("base-de-conhecimento/configuracao/", knowledge_base_views.knowledge_base_config, name="knowledge_base_config"),
    path("base-de-conhecimento/busca/", knowledge_base_views.knowledge_base_diagnostic, name="knowledge_base_diagnostic"),
    path("base-de-conhecimento/eventos/", knowledge_base_views.knowledge_base_events, name="knowledge_base_events"),
    path("base-de-conhecimento/atualizacao/", knowledge_base_views.knowledge_base_operations, name="knowledge_base_operations"),
    path("base-de-conhecimento/atualizacao/solicitar/", knowledge_base_views.knowledge_base_operation_submit, name="knowledge_base_operation_submit"),
    path("base-de-conhecimento/atualizacao/<int:pk>/", knowledge_base_views.knowledge_base_operation_detail, name="knowledge_base_operation_detail"),
    path("<slug:section>/", views.placeholder, name="placeholder"),
]
