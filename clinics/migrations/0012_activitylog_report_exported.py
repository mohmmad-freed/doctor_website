# Generated for the secretary-portal data-export audit (REPORT_EXPORTED action).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clinics', '0011_activitylog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='activitylog',
            name='action',
            field=models.CharField(choices=[('APPOINTMENT_CREATED', 'Appointment created'), ('APPOINTMENT_RESCHEDULED', 'Appointment rescheduled'), ('APPOINTMENT_STATUS_CHANGED', 'Appointment status changed'), ('APPOINTMENT_DELETED', 'Appointment deleted'), ('INVOICE_OPENED', 'Billing session opened'), ('INVOICE_CHARGE_ADDED', 'Charge added'), ('INVOICE_CHARGE_REMOVED', 'Charge removed'), ('INVOICE_DELETED', 'Invoice deleted'), ('PAYMENT_RECORDED', 'Payment recorded'), ('CLINICAL_NOTE_VIEWED', 'Clinical note viewed'), ('PATIENT_REGISTERED', 'Patient registered in clinic'), ('PATIENT_UPDATED', 'Patient record updated'), ('REPORT_EXPORTED', 'Report exported (CSV)')], max_length=32),
        ),
    ]
