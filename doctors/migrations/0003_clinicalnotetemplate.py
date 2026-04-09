from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("doctors", "0002_doctorintakeformtemplate_reason_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClinicalNoteTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("description", models.TextField(blank=True)),
                ("template_type", models.CharField(
                    choices=[("SYSTEM", "System Template"), ("CUSTOM", "Custom Template")],
                    default="CUSTOM",
                    max_length=10,
                )),
                ("doctor", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="clinical_note_templates",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("is_system_default", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Clinical Note Template",
                "verbose_name_plural": "Clinical Note Templates",
                "ordering": ["template_type", "name"],
            },
        ),
        migrations.CreateModel(
            name="ClinicalNoteTemplateElement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("element_type", models.CharField(
                    choices=[
                        ("SUBJECTIVE",   "S — Subjective"),
                        ("OBJECTIVE",    "O — Objective"),
                        ("ASSESSMENT",   "A — Assessment"),
                        ("PLAN",         "P — Plan"),
                        ("FREE_TEXT",    "Free Text"),
                        ("VITALS",       "Vitals"),
                        ("BODY_DIAGRAM", "Body Diagram"),
                        ("DENTAL",       "Dental Chart"),
                    ],
                    max_length=20,
                )),
                ("order", models.PositiveIntegerField(default=0)),
                ("template", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="elements",
                    to="doctors.clinicalnotetemplate",
                )),
            ],
            options={
                "verbose_name": "Template Element",
                "verbose_name_plural": "Template Elements",
                "ordering": ["template", "order"],
                "unique_together": {("template", "element_type")},
            },
        ),
        migrations.CreateModel(
            name="DoctorClinicalNoteSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("doctor", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="clinical_note_settings",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("active_template", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="activated_by",
                    to="doctors.clinicalnotetemplate",
                )),
            ],
            options={
                "verbose_name": "Doctor Clinical Note Settings",
                "verbose_name_plural": "Doctor Clinical Note Settings",
            },
        ),
    ]
