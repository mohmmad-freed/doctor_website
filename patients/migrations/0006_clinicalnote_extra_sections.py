from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0005_add_clinicpatient_file_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="clinicalnote",
            name="extra_sections",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
