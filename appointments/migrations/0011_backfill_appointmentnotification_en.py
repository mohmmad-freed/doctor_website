# Backfill English title/message for notifications created before bilingual storage.
#
# Self-contained (no import of app service code) per data-migration convention.
# Titles map exactly from the known finite set of stored Arabic titles.
# Messages are rebuilt best-effort from the live appointment; types whose
# original text embedded now-unrecoverable data (reschedule / patient-edited:
# the OLD date & time) are left empty so the model falls back to the true
# Arabic original.

from django.db import migrations


TITLE_MAP = {
    "تم استلام طلب الحجز": "Booking Request Received",
    "تم تأكيد موعدك": "Your Appointment Is Confirmed",
    "تم إلغاء موعدك": "Your Appointment Was Cancelled",
    "تم تعديل موعدك": "Your Appointment Was Rescheduled",
    "إلغاء موعد من قبل المريض": "Appointment Cancelled by Patient",
    "تعديل موعد من قبل المريض": "Appointment Edited by Patient",
    "تذكير بموعدك": "Appointment Reminder",
    "حجز جديد بانتظار المراجعة": "New Booking — Pending Review",
    "حجز موعد جديد": "New Appointment Booked",
    "تم تحديث حالة موعدك": "Your Appointment Status Was Updated",
}

# Arabic titles whose English message can be faithfully rebuilt from the
# current appointment (no lost data). Reschedule / patient-edited are absent
# on purpose -> message_en stays "" -> Arabic fallback.
RECONSTRUCTABLE = {
    "تم استلام طلب الحجز",
    "تم تأكيد موعدك",
    "تم إلغاء موعدك",
    "إلغاء موعد من قبل المريض",
    "تذكير بموعدك",
    "حجز جديد بانتظار المراجعة",
    "حجز موعد جديد",
    "تم تحديث حالة موعدك",
}


def _build_message_en(title, appt):
    if appt is None or title not in RECONSTRUCTABLE:
        return ""
    doctor_name = appt.doctor.name if appt.doctor_id else "the doctor"
    clinic_name = appt.clinic.name if appt.clinic_id else ""
    patient_name = appt.patient.name if appt.patient_id else "the patient"
    date_str = appt.appointment_date.strftime("%Y-%m-%d") if appt.appointment_date else ""
    time_str = appt.appointment_time.strftime("%H:%M") if appt.appointment_time else ""

    if title == "تم استلام طلب الحجز":
        return (
            f"Your booking request with {doctor_name} on {date_str} "
            f"at {time_str} at {clinic_name} has been received. "
            f"It is under review by the secretary."
        )
    if title == "تم تأكيد موعدك":
        return (
            f"Your appointment with {doctor_name} on {date_str} "
            f"at {time_str} at {clinic_name} has been confirmed."
        )
    if title == "تم إلغاء موعدك":
        return (
            f"Your appointment with Dr. {doctor_name} on {date_str} "
            f"at {time_str} at {clinic_name} has been cancelled."
        )
    if title == "إلغاء موعد من قبل المريض":
        return (
            f"Patient {patient_name} cancelled their appointment on "
            f"{date_str} at {time_str} at {clinic_name}."
        )
    if title == "تذكير بموعدك":
        return (
            f"Reminder: you have an appointment with {doctor_name} on "
            f"{date_str} at {time_str} at {clinic_name}."
        )
    if title == "حجز جديد بانتظار المراجعة":
        return (
            f"Patient {patient_name} booked an appointment with "
            f"{doctor_name} on {date_str} at {time_str} at {clinic_name}. "
            f"The booking is pending and needs confirmation."
        )
    if title == "حجز موعد جديد":
        return (
            f"A new appointment was booked for patient {patient_name} "
            f"with {doctor_name} on {date_str} at {time_str} "
            f"at {clinic_name}."
        )
    if title == "تم تحديث حالة موعدك":
        status_label = appt.get_status_display() if hasattr(appt, "get_status_display") else ""
        return (
            f"Your appointment with {doctor_name} on {date_str} "
            f"at {time_str} at {clinic_name} was updated to: {status_label}."
        )
    return ""


def forwards(apps, schema_editor):
    Notification = apps.get_model("appointments", "AppointmentNotification")
    qs = (
        Notification.objects.select_related(
            "appointment",
            "appointment__doctor",
            "appointment__clinic",
            "appointment__patient",
        )
        .all()
        .iterator(chunk_size=500)
    )

    batch = []
    for obj in qs:
        obj.title_en = TITLE_MAP.get(obj.title, "")
        obj.message_en = _build_message_en(obj.title, obj.appointment)
        batch.append(obj)
        if len(batch) >= 500:
            Notification.objects.bulk_update(batch, ["title_en", "message_en"])
            batch = []
    if batch:
        Notification.objects.bulk_update(batch, ["title_en", "message_en"])


class Migration(migrations.Migration):

    dependencies = [
        ("appointments", "0010_appointmentnotification_message_en_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
