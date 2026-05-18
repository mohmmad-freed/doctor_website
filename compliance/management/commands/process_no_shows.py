from django.core.management.base import BaseCommand
from appointments.models import Appointment
from compliance.services.compliance_service import apply_due_no_shows

class Command(BaseCommand):
    help = 'Backstop sweep: marks overdue appointments as no-shows per each clinic setting.'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting no-show processing...")
        before = Appointment.objects.filter(status=Appointment.Status.NO_SHOW).count()
        apply_due_no_shows(Appointment.objects.all())
        after = Appointment.objects.filter(status=Appointment.Status.NO_SHOW).count()
        self.stdout.write(self.style.SUCCESS(
            f'Successfully processed and marked {after - before} new no-show appointments.'
        ))
