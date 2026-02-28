import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clinics", "0005_alter_clinicactivationcode_max_doctors_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClinicVerification",
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
                ("owner_phone_verified_at", models.DateTimeField(blank=True, null=True)),
                ("owner_email_verified_at", models.DateTimeField(blank=True, null=True)),
                ("clinic_phone_verified_at", models.DateTimeField(blank=True, null=True)),
                ("clinic_email_verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "clinic",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="verification",
                        to="clinics.clinic",
                    ),
                ),
            ],
            options={
                "verbose_name": "Clinic Verification",
                "verbose_name_plural": "Clinic Verifications",
            },
        ),
    ]
