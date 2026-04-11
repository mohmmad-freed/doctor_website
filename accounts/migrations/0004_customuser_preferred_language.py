from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_alter_customuser_national_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="preferred_language",
            field=models.CharField(
                blank=True,
                choices=[("ar", "Arabic / العربية"), ("en", "English")],
                help_text="User's preferred UI language. Null means role-based default applies.",
                max_length=5,
                null=True,
            ),
        ),
    ]
