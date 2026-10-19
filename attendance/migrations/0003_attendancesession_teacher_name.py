from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("attendance", "0002_student_emergency_contact_student_phone")]

    operations = [
        migrations.AddField(
            model_name="attendancesession",
            name="teacher_name",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
