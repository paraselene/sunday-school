from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from attendance.views import login_view


urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", login_view, name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("attendance.urls")),
]
