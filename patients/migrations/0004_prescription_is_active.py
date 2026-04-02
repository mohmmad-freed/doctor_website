from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0003_clinical_records"),
    ]

    operations = [
        migrations.AddField(
            model_name="prescription",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
    ]
