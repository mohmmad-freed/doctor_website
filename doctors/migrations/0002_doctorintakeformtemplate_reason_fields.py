from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("doctors", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="doctorintakeformtemplate",
            name="show_reason_field",
            field=models.BooleanField(
                default=True,
                help_text="Show the 'describe your condition' textarea at the bottom of the form.",
            ),
        ),
        migrations.AddField(
            model_name="doctorintakeformtemplate",
            name="reason_field_label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Override label. Leave blank for default.",
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="doctorintakeformtemplate",
            name="reason_field_placeholder",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Override placeholder. Leave blank for default.",
                max_length=300,
            ),
        ),
        migrations.AddField(
            model_name="doctorintakeformtemplate",
            name="reason_field_required",
            field=models.BooleanField(
                default=False,
                help_text="Make the reason field required.",
            ),
        ),
    ]
