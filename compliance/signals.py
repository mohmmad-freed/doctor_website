from django.db.models.signals import post_save
from django.dispatch import receiver
from clinics.models import Clinic
from compliance.models import ClinicComplianceSettings

@receiver(post_save, sender=Clinic)
def create_default_compliance_settings(sender, instance, created, **kwargs):
    """
    Automatically creates default ClinicComplianceSettings when a new Clinic is created.
    """
    if created:
        ClinicComplianceSettings.objects.get_or_create(clinic=instance)
