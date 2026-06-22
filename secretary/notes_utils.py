"""
Notes helpers for the secretary portal.

Mirrors the map-style helpers in :mod:`secretary.billing` (``debt_map`` /
``open_invoice_map``): a couple of aggregate queries for a whole page of
appointments so the waiting-room board can show a "has notes" reminder badge
without an N+1 query per card.

"Secretary-visible" notes are ``StaffNote`` rows with audience ``DOCTOR`` or
``SECRETARY`` (``DOCTOR_PRIVATE`` is the doctor's private note and never counted),
plus the legacy free-text ``Appointment.secretary_note`` / ``doctor_note`` fields.
The doctor's clinical ``Appointment.notes`` field is intentionally excluded.
"""

from django.db.models import Count

from patients.models import StaffNote

# Audiences a secretary is allowed to see (mirrors the appointment_overview filter).
_SECRETARY_VISIBLE = [StaffNote.Audience.DOCTOR, StaffNote.Audience.SECRETARY]


def annotate_notes_count(appts, clinic):
    """Set ``appt.notes_count`` on every Appointment in ``appts``.

    The count combines, for each appointment:
      * secretary-visible ``StaffNote`` rows attached to the appointment,
      * secretary-visible ``StaffNote`` rows on the patient profile
        (``appointment IS NULL``) within ``clinic``,
      * legacy ``secretary_note`` / ``doctor_note`` text (one each, if non-empty).

    Two aggregate queries total, regardless of how many appointments — no N+1.
    The value is stored as an attribute on each model instance so a single badge
    partial can read ``appt.notes_count`` across all waiting-room columns (some
    columns wrap the appointment in a dict, but the attribute travels with it).
    """
    appts = list(appts)
    if not appts:
        return appts

    appt_ids = [a.id for a in appts]
    patient_ids = [a.patient_id for a in appts]

    appt_counts = {}     # {appointment_id: n} — StaffNotes on the appointment
    profile_counts = {}  # {patient_id: n}     — StaffNotes on the patient profile
    if appt_ids:
        appt_counts = {
            r["appointment_id"]: r["n"]
            for r in StaffNote.objects.filter(
                appointment_id__in=appt_ids, audience__in=_SECRETARY_VISIBLE
            )
            .values("appointment_id")
            .annotate(n=Count("id"))
        }
    if patient_ids:
        profile_counts = {
            r["patient_id"]: r["n"]
            for r in StaffNote.objects.filter(
                clinic=clinic,
                patient_id__in=patient_ids,
                appointment__isnull=True,
                audience__in=_SECRETARY_VISIBLE,
            )
            .values("patient_id")
            .annotate(n=Count("id"))
        }

    for a in appts:
        legacy = (1 if a.secretary_note else 0) + (1 if a.doctor_note else 0)
        a.notes_count = (
            appt_counts.get(a.id, 0)
            + profile_counts.get(a.patient_id, 0)
            + legacy
        )
    return appts
