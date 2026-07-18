from django.urls import path

from . import views

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
    path("<slug:section>/", views.placeholder, name="placeholder"),
]
