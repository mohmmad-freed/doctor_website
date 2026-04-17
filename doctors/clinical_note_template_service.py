from django.db import transaction
from django.core.exceptions import ValidationError
from doctors.models import ClinicalNoteTemplate, ClinicalNoteTemplateElement

@transaction.atomic
def create_clinical_note_template(doctor, name, description, section_types, section_labels):
    name = name.strip()
    if not name:
        raise ValidationError("Template name is required.")
        
    _validate_sections(section_types, section_labels)
    
    tpl = ClinicalNoteTemplate.objects.create(
        doctor=doctor,
        name=name,
        description=description,
        template_type=ClinicalNoteTemplate.TemplateType.CUSTOM,
    )
    
    _save_elements(tpl, section_types, section_labels)
    return tpl

@transaction.atomic
def update_clinical_note_template(template_id, doctor, name, description, section_types, section_labels):
    name = name.strip()
    if not name:
        raise ValidationError("Template name is required.")
        
    _validate_sections(section_types, section_labels)
    
    # Ensure doctor owns it
    tpl = ClinicalNoteTemplate.objects.get(id=template_id, doctor=doctor)
    tpl.name = name
    tpl.description = description
    tpl.save()
    
    # Safe replace-all strategy
    tpl.elements.all().delete()
    _save_elements(tpl, section_types, section_labels)
    return tpl

def _validate_sections(section_types, section_labels):
    if not section_types or len(section_types) == 0:
        raise ValidationError("At least one section is required.")
        
    if len(section_types) != len(section_labels):
        raise ValidationError("Mismatch between section types and names.")
        
    valid_types = set(ClinicalNoteTemplateElement.ElementType.values)
    
    for idx, et in enumerate(section_types):
        if et not in valid_types:
            raise ValidationError(f"Invalid section type: {et}")
            
        label = section_labels[idx].strip()
        if len(label) > 100:
            raise ValidationError(f"Section label '{label[:10]}...' is too long. Max 100 characters allowed.")

def _save_elements(tpl, section_types, section_labels):
    for idx, (et, label) in enumerate(zip(section_types, section_labels)):
        et = et.strip()
        label = label.strip()
        
        # If legacy sections pass label='', we just naturally save blank text.
        ClinicalNoteTemplateElement.objects.create(
            template=tpl,
            element_type=et,
            custom_label=label,
            order=idx
        )
