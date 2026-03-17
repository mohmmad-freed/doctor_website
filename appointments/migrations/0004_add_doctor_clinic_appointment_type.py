from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("appointments", "0003_reminder_sent_notification_improvements"),
        ("clinics", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DoctorClinicAppointmentType",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="مفعّل")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "appointment_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="doctor_assignments",
                        to="appointments.appointmenttype",
                        verbose_name="نوع الموعد",
                    ),
                ),
                (
                    "clinic",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="doctor_appointment_types",
                        to="clinics.clinic",
                        verbose_name="العيادة",
                    ),
                ),
                (
                    "doctor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="clinic_appointment_types",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="الطبيب",
                    ),
                ),
            ],
            options={
                "verbose_name": "Doctor Appointment Type",
                "verbose_name_plural": "Doctor Appointment Types",
                "ordering": ["clinic", "doctor", "appointment_type__name"],
            },
        ),
        migrations.AddConstraint(
            model_name="doctorclinicappointmenttype",
            constraint=models.UniqueConstraint(
                fields=["doctor", "clinic", "appointment_type"],
                name="unique_doctor_clinic_appointment_type",
            ),
        ),
    ]
