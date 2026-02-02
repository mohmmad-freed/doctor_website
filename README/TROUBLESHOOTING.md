# Troubleshooting Guide

## 1. Common Operational Issues

### "I cannot find a patient I just created."
**Symptom**: Receptionist registers a patient, but they don't appear in the "Clinic Patients" list.
**Cause**:
1.  Did you click "Link Patient to Clinic"? If the patient already existed globally, you must explicitly link them to your clinic via `ClinicPatient`.
2.  Did you register them under a different `clinic_id` by mistake (e.g., using a Super Admin account)?
**Fix**: Go to "Global Search", find the user by mobile, and click "Add to My Clinic".

### "Doctor is not appearing in the booking calendar."
**Symptom**: The doctor exists in the system but shows no available slots.
**Cause**:
1.  **No Schedule**: Has the doctor (or admin) configured their `DoctorAvailability` for this specific clinic?
2.  **Date Match**: Is the `start_date` / `end_date` of the schedule valid for the current week?
3.  **Active Status**: Is `DoctorProfile.is_active` set to True?
**Fix**: Check `Doctors -> [Name] -> Manage Availability`.

### "System says 'Doctor Unavailable' but the slot looks empty."
**Symptom**: Trying to book 10:00 AM for Dr. X, slot looks free in THIS clinic, but system rejects it.
**Cause**:
1.  **Global Conflict**: Dr. X has a confirmed appointment at **ANOTHER** clinic at 10:00 AM.
2.  **Time Buffers**: Is there a "buffer time" setting (e.g., 15 mins between appointments) that creates this conflict?
**Fix**: Check the doctor's GLOBAL schedule (Super Admin view) to confirm availability.

## 2. Dev & Deployment Issues

### "Data from Clinic A is showing in Clinic B!"
**Severity**: CRITICAL.
**Cause**:
1.  **Missing Filter**: A Django view or API endpoint forgot `.filter(clinic=request.clinic)`.
2.  **Shared Cache**: Using a Redis cache key without including `clinic_id` (e.g., `cache.get('daily_appointments')` vs `cache.get(f'daily_appointments_{clinic_id}')`).
**Fix**:
1.  Audit the View/ViewSet code. Ensure `get_queryset` ALWAYS starts with `self.queryset.filter(clinic=...)`.
2.  Clear cache.

### "Migration Failures"
**Symptom**: `relation "clinics_clinic" does not exist`.
**Cause**: Circular dependency between `users` and `clinics` app.
**Fix**:
1.  Comment out the foreign key temporarily.
2.  Run `makemigrations accounts`.
3.  Run `makemigrations clinics`.
4.  Uncomment and run `migrate`.
