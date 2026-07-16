from django.urls import path

from .views import demo_page, widget_config, widget_js

app_name = "widget"

urlpatterns = [
    path("widget.js", widget_js, name="widget_js"),
    path("api/widget/config/", widget_config, name="widget_config"),
    path("demo/", demo_page, name="demo_page"),
]
