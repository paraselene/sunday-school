from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("attendance", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="student",
            name="emergency_contact",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="student",
            name="phone",
            field=models.CharField(blank=True, default="", max_length=30),
        ),
    ]
