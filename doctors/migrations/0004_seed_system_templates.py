"""
Data migration: seed the built-in system Clinical Note Templates.
"""
from django.db import migrations


SYSTEM_TEMPLATES = [
    {
        "name": "Default Template",
        "description": "Standard SOAP format with a free-text section. Used when no custom template is active.",
        "is_system_default": True,
        "elements": ["SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN", "FREE_TEXT"],
    },
    {
        "name": "General Template",
        "description": "General-purpose SOAP clinical note template suitable for most outpatient visits.",
        "is_system_default": False,
        "elements": ["SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN", "FREE_TEXT"],
    },
    {
        "name": "Orthopedic Template",
        "description": "Orthopedic-focused template with a body diagram block for marking injury or pain locations.",
        "is_system_default": False,
        "elements": ["SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN", "VITALS", "BODY_DIAGRAM", "FREE_TEXT"],
    },
    {
        "name": "Dental Template",
        "description": "Dental-focused template with a dental chart block for tooth-level documentation.",
        "is_system_default": False,
        "elements": ["SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN", "DENTAL", "FREE_TEXT"],
    },
]


def seed_templates(apps, schema_editor):
    ClinicalNoteTemplate = apps.get_model("doctors", "ClinicalNoteTemplate")
    ClinicalNoteTemplateElement = apps.get_model("doctors", "ClinicalNoteTemplateElement")

    for tpl_data in SYSTEM_TEMPLATES:
        tpl = ClinicalNoteTemplate.objects.create(
            name=tpl_data["name"],
            description=tpl_data["description"],
            template_type="SYSTEM",
            doctor=None,
            is_system_default=tpl_data["is_system_default"],
        )
        for idx, element_type in enumerate(tpl_data["elements"]):
            ClinicalNoteTemplateElement.objects.create(
                template=tpl,
                element_type=element_type,
                order=idx,
            )


def unseed_templates(apps, schema_editor):
    ClinicalNoteTemplate = apps.get_model("doctors", "ClinicalNoteTemplate")
    ClinicalNoteTemplate.objects.filter(template_type="SYSTEM").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("doctors", "0003_clinicalnotetemplate"),
    ]

    operations = [
        migrations.RunPython(seed_templates, reverse_code=unseed_templates),
    ]
