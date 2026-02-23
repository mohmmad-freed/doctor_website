import traceback
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clinic_website.settings')
django.setup()

from django.contrib.auth import get_user_model
from clinics.models import Clinic
from appointments.models import AppointmentType
from appointments.services.booking_service import book_appointment
from datetime import date, time, timedelta

User = get_user_model()

clinic = Clinic.objects.first()
patient = User.objects.filter(role='PATIENT', patient_profile__clinic_compliances__status='BLOCKED').first()
if not patient:
    print("Could not find a blocked patient, trying any patient...")
    patient = User.objects.filter(role='PATIENT').first()

appt_type = AppointmentType.objects.first()
if appt_type:
    clinic = appt_type.clinic
    doctor = appt_type.doctor
else:
    # Manual fallback for empty database scenarios
    doctor = clinic.main_doctor
    from appointments.models import AppointmentType
    appt_type = AppointmentType.objects.create(doctor=doctor, clinic=clinic, name='Test Appt', duration_minutes=30, price=100)

print("--- Testing Booking Flow ---")
print(f"Patient: {patient.name} (Blocked Status: ", end="")

compliance = patient.patient_profile.clinic_compliances.filter(clinic=clinic).first()
if compliance:
    print(f"{compliance.status})")
else:
    print("NO COMPLIANCE RECORD)")

try:
    print("Attempting to book...")
    book_appointment(
        patient=patient,
        doctor_id=doctor.id,
        clinic_id=clinic.id,
        appointment_type_id=appt_type.id,
        appointment_date=date.today() + timedelta(days=1),
        appointment_time=time(10, 0)
    )
    print('SUCCESS: Appointment was booked.')
except Exception as e:
    print('FAILED: Exception caught!')
    print(f"Type: {type(e)}")
    print(f"Message: {e}")
    traceback.print_exc()
