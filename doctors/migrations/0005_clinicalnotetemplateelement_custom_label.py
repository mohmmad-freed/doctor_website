from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("doctors", "0004_seed_system_templates"),
    ]

    operations = [
        migrations.AddField(
            model_name="clinicalnotetemplateelement",
            name="custom_label",
            field=models.CharField(blank=True, max_length=100, default=""),
            preserve_default=False,
        ),
    ]
