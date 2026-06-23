import uuid

from django.db import migrations, models


def populate_display_tokens(apps, schema_editor):
    """Assign a distinct token to every existing clinic before the unique constraint."""
    Clinic = apps.get_model("clinics", "Clinic")
    for clinic in Clinic.objects.filter(display_token__isnull=True):
        clinic.display_token = uuid.uuid4()
        clinic.save(update_fields=["display_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("clinics", "0009_clinic_logo"),
    ]

    operations = [
        # 1. Add nullable, no unique — so existing rows get NULL first.
        migrations.AddField(
            model_name="clinic",
            name="display_token",
            field=models.UUIDField(editable=False, null=True),
        ),
        # 2. Backfill a distinct UUID per existing clinic.
        migrations.RunPython(populate_display_tokens, migrations.RunPython.noop),
        # 3. Lock in the final shape: unique + uuid4 default for new rows.
        migrations.AlterField(
            model_name="clinic",
            name="display_token",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
                help_text="Unguessable token for the public lobby/kiosk waiting-room screen URL.",
            ),
        ),
    ]
