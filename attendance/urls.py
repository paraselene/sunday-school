from django.urls import path

from . import views


urlpatterns = [
    path("", views.home, name="home"),
    path("classes/", views.classes, name="classes"),
    path("students/", views.students, name="students"),
    path("attendance/", views.attendance, name="attendance"),
    path("reports/", views.reports, name="reports"),
    path("reports/pdf/", views.report_pdf, name="report_pdf"),
]
