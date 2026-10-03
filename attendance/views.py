from datetime import timedelta
from hmac import compare_digest
from io import BytesIO
from pathlib import Path
import sqlite3
from xml.sax.saxutils import escape

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.formats import date_format
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
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
                messages.warning(request, _("資料庫備份失敗，請聯絡管理員。"))
            user, _created = get_user_model().objects.get_or_create(username="shared")
            if user.has_usable_password():
                user.set_unusable_password()
                user.save(update_fields=["password"])
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            next_url = request.POST.get("next", "")
            return redirect(next_url if url_has_allowed_host_and_scheme(next_url, {request.get_host()}, require_https=request.is_secure()) else "home")
        messages.error(request, _("密碼不正確。") if settings.LOGIN_PASSWORD else _("登入密碼尚未設定。"))
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
            messages.error(request, _("請輸入班級名稱。"))
        elif Classroom.objects.filter(name__iexact=name).exists():
            messages.error(request, _("這個班級已經存在。"))
        else:
            Classroom.objects.create(name=name)
            messages.success(request, _("班級已建立。"))
            return redirect("classes")
    return render(request, "attendance/classes.html", {"classrooms": Classroom.objects.annotate(student_count=Count("students", filter=Q(students__active=True)))})


def save_student(request, classroom, student=None):
    data = {field: request.POST.get(field, "").strip() for field in ("name", "emergency_contact", "phone")}
    duplicates = Student.objects.filter(classroom=classroom, name__iexact=data["name"])
    if student:
        duplicates = duplicates.exclude(pk=student.pk)
    if not data["name"]:
        messages.error(request, _("請輸入學生姓名。"))
    elif len(data["name"]) > 100 or len(data["emergency_contact"]) > 100 or len(data["phone"]) > 30:
        messages.error(request, _("學生資料太長。"))
    elif duplicates.exists():
        messages.error(request, _("這個班級已有同名學生。"))
    else:
        if student:
            for field, value in data.items():
                setattr(student, field, value)
            student.save(update_fields=list(data))
            messages.success(request, _("已更新 %(student)s。") % {"student": student.name})
        else:
            Student.objects.create(classroom=classroom, **data)
            messages.success(request, _("學生已新增。"))
        return True
    return False


@login_required
def students(request):
    classroom = selected_classroom(request)
    if request.method == "POST" and not classroom:
        messages.error(request, _("請先選擇班級。"))
        return redirect("students")
    if request.method == "POST":
        action = request.POST.get("action")
        if action in {"archive", "unarchive"}:
            student = get_object_or_404(Student, pk=request.POST.get("student"), classroom=classroom, active=action == "archive")
            student.active = action == "unarchive"
            student.save(update_fields=["active"])
            messages.success(request, (_("已封存 %(student)s。") if action == "archive" else _("已取消封存 %(student)s。")) % {"student": student.name})
        else:
            student = get_object_or_404(Student, pk=request.POST.get("student"), classroom=classroom) if action == "edit" else None
            save_student(request, classroom, student)
        return redirect(f"/students/?classroom={classroom.pk}")
    return render(request, "attendance/students.html", {
        "classrooms": Classroom.objects.all(),
        "classroom": classroom,
        "students": classroom.students.filter(active=True) if classroom else [],
        "archived_students": classroom.students.filter(active=False) if classroom else [],
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
        messages.error(request, _("請選擇星期日。"))
    date = requested_date if not invalid_date else next_sunday()
    date = date or next_sunday()
    session = AttendanceSession.objects.filter(classroom=classroom, date=date).first() if classroom else None
    roster = attendance_roster(classroom, session) if classroom else []
    saved = {record.student_id: record.status for record in session.records.all()} if session else {}

    adding_student = request.method == "POST" and request.POST.get("action") == "add_student"
    student_added = False
    if adding_student and classroom and not invalid_date:
        student_added = save_student(request, classroom)
        roster = attendance_roster(classroom, session)
    elif request.method == "POST" and classroom and not invalid_date:
        statuses = {student.id: request.POST.get(f"status_{student.id}") for student in roster}
        if not roster:
            messages.error(request, _("請先新增至少一名現有學生。"))
        elif any(status not in dict(AttendanceRecord.STATUS_CHOICES) for status in statuses.values()):
            messages.error(request, _("儲存前請為每名學生標示出席或缺席。"))
        else:
            with transaction.atomic():
                session, _created = AttendanceSession.objects.get_or_create(classroom=classroom, date=date)
                for student_id, status in statuses.items():
                    AttendanceRecord.objects.update_or_create(session=session, student_id=student_id, defaults={"status": status})
            messages.success(request, _("點名紀錄已儲存。"))
            return redirect(f"/attendance/?classroom={classroom.pk}&date={date.isoformat()}")

    rows = []
    for student in roster:
        status = saved.get(student.id)
        if request.method == "POST":
            status = request.POST.get(f"status_{student.id}", status)
        rows.append({"student": student, "status": status})
    return render(request, "attendance/attendance.html", {
        "classrooms": Classroom.objects.all(), "classroom": classroom, "date": date, "rows": rows,
        "add_student_errors": adding_student and not student_added,
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
    records = list(records)
    classes, weeks, students = {}, {}, {}
    session_ids = set()
    for record in records:
        session_ids.add(record.session_id)
        classroom = classes.setdefault(record.session.classroom_id, {
            "name": record.session.classroom.name, "present": 0, "absent": 0, "sessions": set(), "students": set(),
        })
        week = weeks.setdefault(record.session.date, {"date": record.session.date, "present": 0, "absent": 0})
        student = students.setdefault(record.student_id, {
            "student": record.student, "classroom": record.session.classroom.name,
            "present": 0, "absent": 0, "streak": 0, "last_present": None,
        })
        for group in (classroom, week, student):
            group[record.status] += 1
        classroom["sessions"].add(record.session_id)
        classroom["students"].add(record.student_id)
        student["last_recorded"] = record.session.date
        if record.status == AttendanceRecord.PRESENT:
            student["last_present"] = record.session.date
            student["streak"] = 0
        else:
            student["streak"] += 1
    for group in [*classes.values(), *weeks.values(), *students.values()]:
        group["total"] = group["present"] + group["absent"]
        group["percentage"] = round(group["present"] * 100 / group["total"], 1)
    for classroom in classes.values():
        classroom["sessions"] = len(classroom["sessions"])
        classroom["students"] = len(classroom["students"])
    student_rows = sorted(students.values(), key=lambda row: (row["percentage"], row["student"].name, row["student"].pk))
    present = sum(row["present"] for row in student_rows)
    absent = len(records) - present
    return {
        "records": records, "start": start, "end": end,
        "classroom_id": classroom_id, "student_id": student_id,
        "present": present, "absent": absent, "sessions": len(session_ids),
        "percentage": round(present * 100 / len(records), 1) if records else 0,
        "student_count": len(students),
        "class_summaries": sorted(classes.values(), key=lambda row: row["name"]),
        "weekly_summaries": list(weeks.values()),
        "student_summaries": student_rows,
        "follow_up": sorted(
            (row for row in student_rows if row["student"].active and row["streak"] >= 2),
            key=lambda row: (-row["streak"], row["student"].name),
        ),
    }


@login_required
def reports(request):
    context = report_data(request)
    context.update({"classrooms": Classroom.objects.all(), "students": Student.objects.select_related("classroom")})
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
    story = [Paragraph(_("主日學點名報表"), styles["Title"]), Spacer(1, 4 * mm)]
    filters = _("日期：%(start)s 至 %(end)s") % {"start": date_format(data["start"], "DATE_FORMAT"), "end": date_format(data["end"], "DATE_FORMAT")}
    if data["classroom_id"]:
        filters += _("｜班級：%(classroom)s") % {"classroom": Classroom.objects.filter(pk=data["classroom_id"]).values_list("name", flat=True).first() or _("未知")}
    if data["student_id"]:
        filters += _("｜學生：%(student)s") % {"student": Student.objects.filter(pk=data["student_id"]).values_list("name", flat=True).first() or _("未知")}
    story += [Paragraph(escape(filters), styles["Normal"]), Paragraph(_("產生時間：%(time)s") % {"time": date_format(timezone.localtime(), "DATETIME_FORMAT")}, styles["Normal"]), Spacer(1, 5 * mm)]
    story += [Paragraph(
        _("出席：%(present)s｜缺席：%(absent)s｜已記錄堂數：%(sessions)s｜出席率：%(percentage).1f%%") % data,
        styles["Heading3"]),
        Paragraph(_("統計只計算已儲存的點名紀錄；未點名的日期不算缺席。"), styles["Normal"]),
        Spacer(1, 4 * mm)]

    def summary_table(title, headings, rows, widths):
        story.append(Paragraph(title, styles["Heading3"]))
        header_style = styles["Normal"].clone("TableHeader", textColor=colors.white)
        cells = [[Paragraph(escape(str(value)), header_style) for value in headings]]
        cells += [[Paragraph(escape(str(value)), styles["Normal"]) for value in row] for row in rows]
        table = Table(cells, repeatRows=1, colWidths=[width * mm for width in widths])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#5b2a86")),
            ("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#ded5e6")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f4fa")]),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.extend([table, Spacer(1, 4 * mm)])

    if not data["records"]:
        story.append(Paragraph(_("沒有符合篩選條件的點名紀錄。"), styles["Normal"]))
    else:
        story.append(Paragraph(_("關懷提醒：現有學生在篩選期間內，最後連續兩堂或以上已記錄的課堂缺席。"), styles["Normal"]))
        if data["follow_up"]:
            summary_table(_("關懷提醒"), [_("學生"), _("班級"), _("連續缺席堂數"), _("最後出席")], [
                [row["student"].name, row["classroom"], row["streak"], row["last_present"].isoformat() if row["last_present"] else _("期間內未曾出席")]
                for row in data["follow_up"]
            ], [50, 45, 35, 50])
        else:
            story.append(Paragraph(_("沒有學生符合關懷提醒條件。"), styles["Normal"]))
        summary_table(_("班級概況"), [_("班級"), _("學生人數"), _("已記錄堂數"), _("出席人次"), _("出席率")], [
            [row["name"], row["students"], row["sessions"], row["present"], f'{row["percentage"]:.1f}%'] for row in data["class_summaries"]
        ], [60, 30, 30, 30, 30])
        summary_table(_("每週趨勢"), [_("日期"), _("出席人次"), _("缺席人次"), _("出席率")], [
            [row["date"].isoformat(), row["present"], row["absent"], f'{row["percentage"]:.1f}%'] for row in data["weekly_summaries"]
        ], [60, 40, 40, 40])
        summary_table(_("學生出席概況"), [_("學生"), _("班級"), _("出席／已記錄"), _("出席率"), _("最後出席")], [
            [row["student"].name, row["classroom"], f'{row["present"]}/{row["total"]}', f'{row["percentage"]:.1f}%', row["last_present"].isoformat() if row["last_present"] else _("期間內未曾出席")]
            for row in data["student_summaries"]
        ], [45, 40, 30, 25, 40])
    document.build(story)
    return HttpResponse(buffer.getvalue(), content_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="attendance-report.pdf"'})
