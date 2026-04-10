from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('appointments', '0006_alter_appointmentnotification_context_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='appointment',
            name='checked_in_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text='Timestamp set automatically when the appointment status transitions to CHECKED_IN.',
            ),
        ),
        migrations.AddField(
            model_name='appointment',
            name='cancellation_reason',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Reason provided by the secretary or patient when cancelling.',
            ),
        ),
    ]
