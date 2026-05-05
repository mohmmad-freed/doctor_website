from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('appointments', '0008_appointment_is_walk_in'),
    ]

    operations = [
        migrations.AddField(
            model_name='appointment',
            name='queue_priority',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text='Manual queue position (1 = first). Assigned on check-in, updated by secretary drag-reorder.',
            ),
        ),
    ]
