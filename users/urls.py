from django.urls import re_path

from .views import GitHubAuthorizeView, GitHubCallbackView, LogoutView, RefreshTokenView

urlpatterns = [
    re_path(r"^github/?$", GitHubAuthorizeView.as_view(), name="github-authorize"),
    re_path(r"^github/callback/?$", GitHubCallbackView.as_view(), name="github-callback"),
    re_path(r"^refresh/?$", RefreshTokenView.as_view(), name="token-refresh"),
    re_path(r"^logout/?$", LogoutView.as_view(), name="logout"),
]
