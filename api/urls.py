from django.urls import path, re_path

from .views import ProfileDetailView, ProfileExportView, ProfileSearchView, ProfileView
from users.views import UserMeView

urlpatterns = [
    re_path(r"^users/me/?$", UserMeView.as_view(), name="user-me"),
    path("profiles/export/", ProfileExportView.as_view(), name="profiles-export"),
    path("profiles/search/", ProfileSearchView.as_view(), name="profiles-search"),
    path("profiles/", ProfileView.as_view(), name="profiles"),
    path("profiles/<uuid:id>/", ProfileDetailView.as_view(), name="profile-detail"),
]
