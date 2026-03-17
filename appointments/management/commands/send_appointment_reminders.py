"""
Management command: send_appointment_reminders

Finds appointments within the next REMINDER_HOURS_BEFORE hours that:
- Have status CONFIRMED
- Have reminder_sent=False

Creates reminder notifications (in-app + email) and marks reminder_sent=True.
Safe to run multiple times (idempotent) because reminder_sent=True blocks re-sending.

Usage:
    python manage.py send_appointment_reminders
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from appointments.models import Appointment
from appointments.services.appointment_notification_service import notify_appointment_reminder

logger = logging.getLogger(__name__)

# Number of hours ahead to look for appointments when sending reminders.
REMINDER_HOURS_BEFORE = 24


class Command(BaseCommand):
    help = (
        f"Send reminder notifications for appointments in the next "
        f"{REMINDER_HOURS_BEFORE} hours. Idempotent — safe to run multiple times."
    )

    def handle(self, *args, **options):
        now = timezone.now()
        window_end = now + timedelta(hours=REMINDER_HOURS_BEFORE)

        # Find appointments that:
        # - are scheduled within the reminder window (now < appointment_datetime <= window_end)
        # - have status CONFIRMED
        # - have not yet had a reminder sent
        #
        # Because appointment_date and appointment_time are stored as separate fields
        # we filter by date range conservatively and then rely on reminder_sent to
        # prevent duplicates.
        from django.db.models import Q
        from datetime import datetime as dt_cls

        today = now.date()
        window_end_date = window_end.date()

        appointments = (
            Appointment.objects.filter(
                appointment_date__gte=today,
                appointment_date__lte=window_end_date,
                status=Appointment.Status.CONFIRMED,
                reminder_sent=False,
            )
            .select_related("patient", "doctor", "clinic")
        )

        total = appointments.count()
        self.stdout.write(
            f"Found {total} appointment(s) eligible for reminders "
            f"(window: {now.isoformat()} → {window_end.isoformat()})."
        )

        sent_count = 0
        skipped_count = 0

        for appointment in appointments:
            # Calculate the appointment datetime (naive, in local time)
            appt_local_naive = dt_cls.combine(
                appointment.appointment_date, appointment.appointment_time
            )
            # Convert now to naive local time for comparison
            local_now_naive = timezone.localtime(now).replace(tzinfo=None)
            local_window_end_naive = timezone.localtime(window_end).replace(tzinfo=None)

            # Only send reminder if appointment is within the window
            if not (local_now_naive < appt_local_naive <= local_window_end_naive):
                skipped_count += 1
                continue

            # Double-check reminder_sent (race-condition guard)
            if appointment.reminder_sent:
                skipped_count += 1
                continue

            try:
                notify_appointment_reminder(appointment)
                appointment.reminder_sent = True
                appointment.save(update_fields=["reminder_sent", "updated_at"])
                sent_count += 1
                logger.info(
                    "[REMINDER] Sent reminder for appointment_id=%s patient_id=%s",
                    appointment.id,
                    appointment.patient_id,
                )
            except Exception as exc:
                logger.error(
                    "[REMINDER] Failed to send reminder for appointment_id=%s: %r",
                    appointment.id,
                    exc,
                )
                self.stderr.write(
                    f"  ERROR: appointment_id={appointment.id} — {exc}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Reminders sent: {sent_count}, skipped: {skipped_count}."
            )
        )
