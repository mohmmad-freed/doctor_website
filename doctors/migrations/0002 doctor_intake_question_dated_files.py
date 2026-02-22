# Add DATED_FILES choice to DoctorIntakeQuestion.field_type

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("doctors", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="doctorintakequestion",
            name="field_type",
            field=models.CharField(
                choices=[
                    ("TEXT", "نص قصير"),
                    ("TEXTAREA", "نص طويل"),
                    ("SELECT", "قائمة منسدلة"),
                    ("MULTISELECT", "اختيار متعدد"),
                    ("CHECKBOX", "مربع اختيار (نعم/لا)"),
                    ("DATE", "تاريخ"),
                    ("FILE", "ملف مرفق"),
                    ("DATED_FILES", "ملفات مؤرخة"),
                ],
                default="TEXT",
                max_length=20,
            ),
        ),
    ]