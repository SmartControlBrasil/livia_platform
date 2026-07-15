from django.contrib import admin
from django.urls import include, path

from .views import healthcheck

urlpatterns = [
    path("health/", healthcheck),
    path("admin/", admin.site.urls),
    path("api/", include("assistant_core.urls")),
    path("", include("widget.urls")),
]
