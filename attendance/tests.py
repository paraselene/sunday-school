from datetime import date
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AttendanceRecord, AttendanceSession, Classroom, Student
from .views import next_sunday, weekly_database_backup


class AttendanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("teacher", password="secret")
        self.classroom = Classroom.objects.create(name="Juniors")
        self.anna = Student.objects.create(name="Anna", classroom=self.classroom)
        self.ben = Student.objects.create(name="Ben", classroom=self.classroom)

    def login(self):
        self.client.force_login(self.user)

    def test_next_sunday_including_sunday(self):
        self.assertEqual(next_sunday(date(2026, 9, 14)), date(2026, 9, 20))
        self.assertEqual(next_sunday(date(2026, 9, 20)), date(2026, 9, 20))

    def test_weekly_backup_runs_once_after_sunday(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "db.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE example (value TEXT)")
                connection.execute("INSERT INTO example VALUES ('保留資料')")
            backup = weekly_database_backup(date(2026, 9, 21), database, Path(directory) / "backups")
            self.assertEqual(backup.name, "sunday-school-2026-09-21.sqlite3")
            self.assertEqual(weekly_database_backup(date(2026, 9, 27), database, backup.parent), backup)
            with sqlite3.connect(backup) as connection:
                self.assertEqual(connection.execute("SELECT value FROM example").fetchone()[0], "保留資料")

    def test_unique_constraints(self):
        session = AttendanceSession.objects.create(classroom=self.classroom, date="2026-09-20")
        AttendanceRecord.objects.create(session=session, student=self.anna, status="present")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceSession.objects.create(classroom=self.classroom, date="2026-09-20")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceRecord.objects.create(session=session, student=self.anna, status="absent")

    def test_login_required(self):
        for name in ["home", "classes", "students", "attendance", "reports", "report_pdf"]:
            self.assertRedirects(self.client.get(reverse(name)), f"/login/?next={reverse(name)}")

    @override_settings(LOGIN_PASSWORD="共同密碼")
    def test_shared_password_login_needs_no_username(self):
        page = self.client.get(reverse("login"))
        self.assertContains(page, "密碼")
        self.assertNotContains(page, "使用者名稱")
        self.assertContains(self.client.post(reverse("login"), {"password": "錯誤"}), "密碼不正確")
        self.assertRedirects(self.client.post(reverse("login"), {"password": "共同密碼"}), reverse("home"))

    def test_add_archive_and_keep_history(self):
        self.login()
        self.client.post(reverse("students"), {"classroom": self.classroom.pk, "name": "Cara"})
        cara = Student.objects.get(name="Cara")
        session = AttendanceSession.objects.create(classroom=self.classroom, date="2026-09-13")
        AttendanceRecord.objects.create(session=session, student=cara, status="present")
        self.client.post(reverse("students"), {"classroom": self.classroom.pk, "student": cara.pk, "action": "archive"})
        future = self.client.get(reverse("attendance"), {"classroom": self.classroom.pk, "date": "2026-09-20"})
        history = self.client.get(reverse("reports"), {"student": cara.pk, "start": "2026-01-01", "end": "2026-12-31"})
        self.assertNotContains(future, "<legend>Cara", html=False)
        self.assertContains(history, "Cara")
        self.assertContains(history, f'value="{cara.pk}" selected')

    def test_save_requires_complete_roster_and_reopens_for_correction(self):
        self.login()
        url = reverse("attendance")
        incomplete = self.client.post(url, {"classroom": self.classroom.pk, "date": "2026-09-20", f"status_{self.anna.pk}": "present"})
        self.assertContains(incomplete, "儲存前請為每名學生標示出席或缺席")
        self.assertFalse(AttendanceSession.objects.exists())
        complete = {"classroom": self.classroom.pk, "date": "2026-09-20", f"status_{self.anna.pk}": "present", f"status_{self.ben.pk}": "absent"}
        self.client.post(url, complete)
        session = AttendanceSession.objects.get()
        self.assertEqual(session.records.count(), 2)
        complete[f"status_{self.ben.pk}"] = "present"
        self.client.post(url, complete)
        self.assertEqual(AttendanceSession.objects.count(), 1)
        self.assertEqual(session.records.get(student=self.ben).status, "present")

    def test_reports_totals_filters_empty_and_pdf(self):
        self.login()
        session = AttendanceSession.objects.create(classroom=self.classroom, date="2026-09-13")
        AttendanceRecord.objects.create(session=session, student=self.anna, status="present")
        AttendanceRecord.objects.create(session=session, student=self.ben, status="absent")
        response = self.client.get(reverse("reports"), {"classroom": self.classroom.pk, "start": "2026-01-01", "end": "2026-12-31"})
        self.assertContains(response, "50.0%")
        self.assertEqual(response.context["sessions"], 1)
        student_response = self.client.get(reverse("reports"), {"student": self.anna.pk, "start": "2026-01-01", "end": "2026-12-31"})
        self.assertEqual(list(student_response.context["records"]), [AttendanceRecord.objects.get(student=self.anna)])
        empty = self.client.get(reverse("reports"), {"start": "2025-01-01", "end": "2025-12-31"})
        self.assertContains(empty, "沒有符合篩選條件的點名紀錄")
        pdf = self.client.get(reverse("report_pdf"), {"start": "2026-01-01", "end": "2026-12-31"})
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertEqual(self.client.get(reverse("reports"), {"student": "invalid"}).status_code, 404)
