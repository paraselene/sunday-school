from datetime import timedelta
from hmac import compare_digest
from io import BytesIO
from pathlib import Path
import sqlite3

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import AttendanceRecord, AttendanceSession, Classroom, Student


def weekly_database_backup(today=None, database_path=None, backup_dir=None):
    today = today or timezone.localdate()
    database_path = Path(database_path or settings.DATABASES["default"]["NAME"])
    if str(database_path).startswith("file:memory") or str(database_path) == ":memory:":
        return None
    backup_dir = Path(backup_dir or database_path.parent / "backups")
    destination = backup_dir / f"sunday-school-{today - timedelta(days=today.weekday())}.sqlite3"
    if destination.exists():
        return destination
    backup_dir.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    try:
        with sqlite3.connect(database_path) as source, sqlite3.connect(temporary) as target:
            source.backup(target)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def login_view(request):
    if request.method == "POST":
        password = request.POST.get("password", "")
        if settings.LOGIN_PASSWORD and compare_digest(password.encode(), settings.LOGIN_PASSWORD.encode()):
            try:
                weekly_database_backup()
            except (OSError, sqlite3.Error):
                messages.warning(request, "資料庫備份失敗，請聯絡管理員。")
            user, _ = get_user_model().objects.get_or_create(username="shared")
            if user.has_usable_password():
                user.set_unusable_password()
                user.save(update_fields=["password"])
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            next_url = request.POST.get("next", "")
            return redirect(next_url if url_has_allowed_host_and_scheme(next_url, {request.get_host()}, require_https=request.is_secure()) else "home")
        messages.error(request, "密碼不正確。" if settings.LOGIN_PASSWORD else "登入密碼尚未設定。")
    return render(request, "registration/login.html", {"next": request.GET.get("next", "")})


def next_sunday(day=None):
    day = day or timezone.localdate()
    return day + timedelta(days=(6 - day.weekday()) % 7)


def selected_classroom(request):
    value = request.GET.get("classroom") or request.POST.get("classroom")
    if value and not value.isdigit():
        raise Http404
    return get_object_or_404(Classroom, pk=value) if value else None


@login_required
def home(request):
    return render(request, "attendance/home.html", {"classrooms": Classroom.objects.all()})


@login_required
def classes(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "請輸入班級名稱。")
        elif Classroom.objects.filter(name__iexact=name).exists():
            messages.error(request, "這個班級已經存在。")
        else:
            Classroom.objects.create(name=name)
            messages.success(request, "班級已建立。")
            return redirect("classes")
    return render(request, "attendance/classes.html", {"classrooms": Classroom.objects.annotate(student_count=Count("students", filter=Q(students__active=True)))})


@login_required
def students(request):
    classroom = selected_classroom(request)
    if request.method == "POST" and not classroom:
        messages.error(request, "請先選擇班級。")
        return redirect("students")
    if request.method == "POST":
        if request.POST.get("action") == "archive":
            student = get_object_or_404(Student, pk=request.POST.get("student"), classroom=classroom, active=True)
            student.active = False
            student.save(update_fields=["active"])
            messages.success(request, f"已封存 {student.name}。")
        else:
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "請輸入學生姓名。")
            elif Student.objects.filter(classroom=classroom, name__iexact=name).exists():
                messages.error(request, "這個班級已有同名學生。")
            else:
                Student.objects.create(name=name, classroom=classroom)
                messages.success(request, "學生已新增。")
        return redirect(f"/students/?classroom={classroom.pk}")
    return render(request, "attendance/students.html", {
        "classrooms": Classroom.objects.all(),
        "classroom": classroom,
        "students": classroom.students.filter(active=True) if classroom else [],
    })


def attendance_roster(classroom, session):
    ids = list(classroom.students.filter(active=True).values_list("id", flat=True))
    if session:
        ids += list(session.records.values_list("student_id", flat=True))
    return Student.objects.filter(id__in=ids).distinct()


@login_required
def attendance(request):
    classroom = selected_classroom(request)
    requested_date = parse_date(request.GET.get("date", "") or request.POST.get("date", ""))
    invalid_date = requested_date and requested_date.weekday() != 6
    if invalid_date:
        messages.error(request, "請選擇星期日。")
    date = requested_date if not invalid_date else next_sunday()
    date = date or next_sunday()
    session = AttendanceSession.objects.filter(classroom=classroom, date=date).first() if classroom else None
    roster = attendance_roster(classroom, session) if classroom else []
    saved = {record.student_id: record.status for record in session.records.all()} if session else {}

    if request.method == "POST" and classroom and not invalid_date:
        statuses = {student.id: request.POST.get(f"status_{student.id}") for student in roster}
        if not roster:
            messages.error(request, "請先新增至少一名現有學生。")
        elif any(status not in dict(AttendanceRecord.STATUS_CHOICES) for status in statuses.values()):
            messages.error(request, "儲存前請為每名學生標示出席或缺席。")
        else:
            with transaction.atomic():
                session, _ = AttendanceSession.objects.get_or_create(classroom=classroom, date=date)
                for student_id, status in statuses.items():
                    AttendanceRecord.objects.update_or_create(session=session, student_id=student_id, defaults={"status": status})
            messages.success(request, "點名紀錄已儲存。")
            return redirect(f"/attendance/?classroom={classroom.pk}&date={date.isoformat()}")

    rows = [{"student": student, "status": saved.get(student.id)} for student in roster]
    return render(request, "attendance/attendance.html", {
        "classrooms": Classroom.objects.all(), "classroom": classroom, "date": date, "rows": rows,
    })


def report_data(request):
    today = timezone.localdate()
    start = parse_date(request.GET.get("start", "")) or today.replace(month=1, day=1)
    end = parse_date(request.GET.get("end", "")) or today
    classroom_id = request.GET.get("classroom", "")
    student_id = request.GET.get("student", "")
    if (classroom_id and not classroom_id.isdigit()) or (student_id and not student_id.isdigit()):
        raise Http404
    records = AttendanceRecord.objects.select_related("session__classroom", "student").filter(session__date__range=(start, end))
    if classroom_id:
        records = records.filter(session__classroom_id=classroom_id)
    if student_id:
        records = records.filter(student_id=student_id)
    records = records.order_by("session__date", "session__classroom__name", "student__name")
    totals = records.aggregate(present=Count("id", filter=Q(status=AttendanceRecord.PRESENT)), absent=Count("id", filter=Q(status=AttendanceRecord.ABSENT)))
    total = totals["present"] + totals["absent"]
    return {
        "records": records,
        "start": start,
        "end": end,
        "classroom_id": classroom_id,
        "student_id": student_id,
        "present": totals["present"],
        "absent": totals["absent"],
        "sessions": records.values("session_id").distinct().count(),
        "percentage": round(totals["present"] * 100 / total, 1) if total else 0,
    }


@login_required
def reports(request):
    context = report_data(request)
    context.update({"classrooms": Classroom.objects.all(), "students": Student.objects.all()})
    return render(request, "attendance/reports.html", context)


@login_required
def report_pdf(request):
    data = report_data(request)
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
    pdfmetrics.registerFont(TTFont("Chinese", settings.PDF_FONT_PATH))
    styles = getSampleStyleSheet()
    for style in (styles["Title"], styles["Normal"], styles["Heading3"]):
        style.fontName = "Chinese"
    story = [Paragraph("主日學點名報表", styles["Title"]), Spacer(1, 4 * mm)]
    filters = f"日期：{data['start']} 至 {data['end']}"
    if data["classroom_id"]:
        filters += f"｜班級：{Classroom.objects.filter(pk=data['classroom_id']).values_list('name', flat=True).first() or '未知'}"
    if data["student_id"]:
        filters += f"｜學生：{Student.objects.filter(pk=data['student_id']).values_list('name', flat=True).first() or '未知'}"
    story += [Paragraph(filters, styles["Normal"]), Paragraph(f"產生時間：{timezone.localtime():%Y-%m-%d %H:%M %Z}", styles["Normal"]), Spacer(1, 5 * mm)]
    rows = [["日期", "班級", "學生", "狀態"]]
    rows += [[str(record.session.date), record.session.classroom.name, record.student.name, record.get_status_display()] for record in data["records"]]
    if len(rows) == 1:
        story.append(Paragraph("沒有符合篩選條件的點名紀錄。", styles["Normal"]))
    else:
        table = Table(rows, repeatRows=1, colWidths=[27 * mm, 42 * mm, 75 * mm, 25 * mm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#5b2a86")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), "Chinese"),
            ("GRID", (0, 0), (-1, -1), .25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f3")]),
        ]))
        story.append(table)
    story += [Spacer(1, 6 * mm), Paragraph(
        f"出席：{data['present']}｜缺席：{data['absent']}｜已記錄堂數：{data['sessions']}｜出席率：{data['percentage']:.1f}%",
        styles["Heading3"],
    )]
    document.build(story)
    return HttpResponse(buffer.getvalue(), content_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="attendance-report.pdf"'})
