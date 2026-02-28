import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clinics", "0003_clinicstaff_add_main_doctor_role_activationcode_add_clinic_fk"),
    ]

    operations = [
        # 1. Add subscription fields to ClinicActivationCode
        migrations.AddField(
            model_name="clinicactivationcode",
            name="plan_type",
            field=models.CharField(
                choices=[("MONTHLY", "Monthly"), ("YEARLY", "Yearly")],
                default="MONTHLY",
                help_text="Subscription plan granted to the clinic.",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="clinicactivationcode",
            name="subscription_expires_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                help_text="When the subscription granted by this code expires.",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="clinicactivationcode",
            name="max_doctors",
            field=models.PositiveIntegerField(
                default=1,
                help_text="Maximum number of doctors allowed under this subscription.",
            ),
        ),
        # 2. Create ClinicSubscription
        migrations.CreateModel(
            name="ClinicSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "clinic",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscription",
                        to="clinics.clinic",
                    ),
                ),
                (
                    "plan_type",
                    models.CharField(
                        choices=[("MONTHLY", "Monthly"), ("YEARLY", "Yearly")],
                        default="MONTHLY",
                        max_length=10,
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                ("max_doctors", models.PositiveIntegerField(default=1)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("ACTIVE", "Active"),
                            ("EXPIRED", "Expired"),
                            ("SUSPENDED", "Suspended"),
                        ],
                        default="ACTIVE",
                        max_length=10,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Clinic Subscription",
                "verbose_name_plural": "Clinic Subscriptions",
            },
        ),
    ]
