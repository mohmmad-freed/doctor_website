from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("doctors", "0005_clinicalnotetemplateelement_custom_label"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="clinicalnotetemplateelement",
            unique_together=set(),
        ),
    ]
