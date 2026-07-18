from django.urls import path

from . import views

app_name = "operations_portal"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("<slug:section>/", views.placeholder, name="placeholder"),
]
