from django.urls import path

from .views import widget_js

app_name = "widget"

urlpatterns = [
    path("widget.js", widget_js, name="widget_js"),
]
