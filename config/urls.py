from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("assistant_core.urls")),
    path("", include("widget.urls")),
]
