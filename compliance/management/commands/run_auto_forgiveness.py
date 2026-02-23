from django.core.management.base import BaseCommand
from compliance.services.compliance_service import run_auto_forgiveness

class Command(BaseCommand):
    help = 'Runs the auto-forgiveness logic for all clinics with the setting enabled.'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting auto-forgiveness processing...")
        run_auto_forgiveness()
        self.stdout.write(self.style.SUCCESS('Successfully ran auto-forgiveness check across all clinics.'))
