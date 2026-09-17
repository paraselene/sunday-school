from django.contrib import admin

from .models import AttendanceRecord, AttendanceSession, Classroom, Student


admin.site.register([Classroom, Student, AttendanceSession, AttendanceRecord])
