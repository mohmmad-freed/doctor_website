from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('patients', '0004_prescription_is_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='clinicpatient',
            name='file_number',
            field=models.CharField(
                blank=True,
                default='',
                max_length=20,
                help_text='Auto-generated per-clinic file number (e.g. 2026-0001). Set by the secretary on registration.',
            ),
        ),
    ]
