from django.core.management.base import BaseCommand
from django.utils import timezone
from appointments.models import Appointment
from compliance.services.compliance_service import process_appointment_no_show

class Command(BaseCommand):
    help = 'Processes past appointments and records no-shows based on grace periods.'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting no-show processing...")
        # Only fetch appointments that are PENDING or CONFIRMED and potentially past due
        appointments = Appointment.objects.filter(
            status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED],
            appointment_date__lte=timezone.now().date()
        )
        
        count = 0
        for appointment in appointments:
            initial_status = appointment.status
            # process_appointment_no_show inherently checks if it exceeds grace_period
            process_appointment_no_show(appointment)
            
            # Simple check if status actually changed to NO_SHOW during this run
            if appointment.status == Appointment.Status.NO_SHOW and initial_status != Appointment.Status.NO_SHOW:
                count += 1
                
        self.stdout.write(self.style.SUCCESS(f'Successfully processed and marked {count} new no-show appointments.'))
