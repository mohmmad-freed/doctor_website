import django.contrib.postgres.fields
from django.db import migrations, models


def populate_roles(apps, schema_editor):
    """
    Back-fill `roles` for every existing user by copying their current primary `role`
    value.  This guarantees that code using `has_role()` / `user.roles` keeps
    working correctly for accounts that existed before this migration ran.
    """
    CustomUser = apps.get_model("accounts", "CustomUser")
    for user in CustomUser.objects.all():
        if not user.roles:
            user.roles = [user.role] if user.role else ["PATIENT"]
            user.save(update_fields=["roles"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="roles",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=20),
                blank=True,
                default=list,
                help_text=(
                    "All roles assigned to this user "
                    "(a user may hold multiple roles simultaneously)."
                ),
                size=None,
            ),
        ),
        migrations.RunPython(populate_roles, migrations.RunPython.noop),
    ]
