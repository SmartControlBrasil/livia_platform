from django.urls import path

from . import views

app_name = "operations_portal"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("conversas/", views.conversation_list, name="conversation_list"),
    path("conversas/<int:pk>/", views.conversation_detail, name="conversation_detail"),
    path("leads/", views.lead_list, name="lead_list"),
    path("leads/<int:pk>/", views.lead_detail, name="lead_detail"),
    path("leads/<int:pk>/reprocessar-crm/", views.retry_lead_crm_dispatch, name="lead_retry_crm"),
    path("<slug:section>/", views.placeholder, name="placeholder"),
]
