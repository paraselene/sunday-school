from django.db import models
from django.utils.translation import gettext_lazy as _


class Classroom(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Student(models.Model):
    name = models.CharField(max_length=100)
    emergency_contact = models.CharField(max_length=100, blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="students")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [models.UniqueConstraint(fields=["classroom", "name"], name="unique_student_name_per_class")]

    def __str__(self):
        return self.name


class AttendanceSession(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="sessions")
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["classroom", "date"], name="unique_classroom_date")]

    def __str__(self):
        return f"{self.classroom} — {self.date}"


class AttendanceRecord(models.Model):
    PRESENT = "present"
    ABSENT = "absent"
    STATUS_CHOICES = [(PRESENT, _("出席")), (ABSENT, _("缺席"))]

    session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name="records")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="attendance_records")
    status = models.CharField(max_length=7, choices=STATUS_CHOICES)

    class Meta:
        ordering = ["session__date", "student__name"]
        constraints = [models.UniqueConstraint(fields=["session", "student"], name="unique_session_student")]

    def __str__(self):
        return f"{self.student}: {self.status}"
