from django.urls import path

from .views import chat_api

app_name = "assistant_core"

urlpatterns = [
    path("chat/", chat_api, name="chat_api"),
]
