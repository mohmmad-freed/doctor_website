# Add file_group_date to AppointmentAttachment for DATED_FILES support

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("appointments", "0006_appointment_patient_edit_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="appointmentattachment",
            name="file_group_date",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Date label for this file group (e.g. date of lab results).",
            ),
        ),
    ]