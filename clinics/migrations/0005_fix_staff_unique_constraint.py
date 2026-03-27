from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Replace the (clinic, user) unique constraint on active ClinicStaff rows
    with a (clinic, user, role) constraint.

    This allows a single person to hold multiple roles at the same clinic
    simultaneously (e.g. both DOCTOR and SECRETARY), while still preventing
    duplicate active entries for the exact same role.
    """

    dependencies = [
        ("clinics", "0004_fix_plan_limits_secretary_default"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="clinicstaff",
            name="unique_active_staff_per_clinic",
        ),
        migrations.AddConstraint(
            model_name="clinicstaff",
            constraint=models.UniqueConstraint(
                fields=["clinic", "user", "role"],
                condition=models.Q(revoked_at__isnull=True),
                name="unique_active_staff_role_per_clinic",
            ),
        ),
    ]
