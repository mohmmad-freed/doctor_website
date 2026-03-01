from django.core.exceptions import ValidationError
from appointments.models import AppointmentType

def get_appointment_types_for_clinic(clinic_id):
    """Get all appointment types for a specific clinic."""
    return AppointmentType.objects.filter(clinic_id=clinic_id).order_by('name')

def create_appointment_type(clinic, data):
    """Create a new appointment type for a clinic."""
    duration_minutes = int(data.get('duration_minutes', 0))
    if duration_minutes <= 0:
        raise ValidationError("يجب أن تكون المدة بالدقائق رقماً صحيحاً موجباً.")
    
    name = data.get('name', '').strip()
    if not name:
        raise ValidationError("اسم نوع الموعد مطلوب.")
        
    if AppointmentType.objects.filter(clinic=clinic, name=name).exists():
        raise ValidationError("يوجد نوع موعد بهذا الاسم مسبقاً في هذه العيادة.")
        
    return AppointmentType.objects.create(
        clinic=clinic,
        name=name,
        name_ar=data.get('name_ar', '').strip(),
        duration_minutes=duration_minutes,
        is_active=data.get('is_active') == 'True' or data.get('is_active') == True or data.get('is_active') == 'on',
        price=data.get('price', 0.0),
        description=data.get('description', '').strip()
    )

def update_appointment_type(clinic, type_id, data):
    """Update an existing appointment type for a clinic."""
    appointment_type = AppointmentType.objects.get(id=type_id, clinic=clinic)
    
    if 'duration_minutes' in data:
        duration_minutes = int(data['duration_minutes'])
        if duration_minutes <= 0:
            raise ValidationError("يجب أن تكون المدة بالدقائق رقماً صحيحاً موجباً.")
        appointment_type.duration_minutes = duration_minutes
    
    if 'name' in data:
        name = data['name'].strip()
        if not name:
            raise ValidationError("اسم نوع الموعد مطلوب.")
            
        if name != appointment_type.name and AppointmentType.objects.filter(clinic=clinic, name=name).exists():
            raise ValidationError("يوجد نوع موعد بهذا الاسم مسبقاً في هذه العيادة.")
        appointment_type.name = name
    
    if 'name_ar' in data:
        appointment_type.name_ar = data['name_ar'].strip()
        
    if 'is_active' in data:
        appointment_type.is_active = data['is_active'] == 'True' or data['is_active'] == True or data.get('is_active') == 'on'
        
    if 'price' in data:
        appointment_type.price = data['price']
        
    if 'description' in data:
        appointment_type.description = data['description'].strip()
        
    appointment_type.save()
    return appointment_type

def toggle_appointment_type_status(clinic, type_id):
    """Toggle the active status of an appointment type."""
    appointment_type = AppointmentType.objects.get(id=type_id, clinic=clinic)
    appointment_type.is_active = not appointment_type.is_active
    appointment_type.save()
    return appointment_type
