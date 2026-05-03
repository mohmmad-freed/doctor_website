from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('appointments', '0007_add_appointment_tracking_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='appointment',
            name='is_walk_in',
            field=models.BooleanField(
                default=False,
                help_text='True when the appointment was registered as a walk-in (no prior booking).',
            ),
        ),
    ]
