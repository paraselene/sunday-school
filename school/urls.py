from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import TemplateView

from attendance.views import login_view


urlpatterns = [
    path("manifest.webmanifest", TemplateView.as_view(template_name="manifest.webmanifest", content_type="application/manifest+json"), name="web_manifest"),
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("login/", login_view, name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("attendance.urls")),
]
