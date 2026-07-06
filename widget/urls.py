from django.urls import path

from .views import demo_page, widget_js

app_name = "widget"

urlpatterns = [
    path("widget.js", widget_js, name="widget_js"),
    path("demo/", demo_page, name="demo_page"),
]
