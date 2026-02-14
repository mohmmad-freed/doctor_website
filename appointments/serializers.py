from rest_framework import serializers
from .models import Appointment, AppointmentType


class AppointmentTypeSerializer(serializers.ModelSerializer):
    """Serializer for appointment types offered by a doctor."""

    doctor_name = serializers.CharField(source="doctor.name", read_only=True)
    clinic_name = serializers.CharField(source="clinic.name", read_only=True)

    class Meta:
        model = AppointmentType
        fields = [
            "id",
            "name",
            "duration_minutes",
            "price",
            "description",
            "doctor",
            "doctor_name",
            "clinic",
            "clinic_name",
            "is_active",
        ]


class BookAppointmentSerializer(serializers.Serializer):
    """
    Request serializer for booking an appointment.

    Validates incoming data from the patient before passing
    to the booking service for business-logic validation.
    """

    doctor_id = serializers.IntegerField()
    clinic_id = serializers.IntegerField()
    appointment_type_id = serializers.IntegerField()
    appointment_date = serializers.DateField(
        help_text="Desired date in YYYY-MM-DD format.",
    )
    appointment_time = serializers.TimeField(
        help_text="Desired start time in HH:MM format.",
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional reason for visit.",
    )


class AppointmentResponseSerializer(serializers.ModelSerializer):
    """
    Response serializer for a booked appointment.

    Returns the full appointment details after a successful booking.
    """

    doctor_name = serializers.CharField(source="doctor.name", read_only=True)
    clinic_name = serializers.CharField(source="clinic.name", read_only=True)
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    appointment_type_name = serializers.CharField(
        source="appointment_type.name", read_only=True
    )
    appointment_type_duration = serializers.IntegerField(
        source="appointment_type.duration_minutes", read_only=True
    )
    appointment_type_price = serializers.DecimalField(
        source="appointment_type.price",
        max_digits=8,
        decimal_places=2,
        read_only=True,
    )
    status_display = serializers.CharField(
        source="get_status_display", read_only=True
    )

    class Meta:
        model = Appointment
        fields = [
            "id",
            "patient",
            "patient_name",
            "doctor",
            "doctor_name",
            "clinic",
            "clinic_name",
            "appointment_type",
            "appointment_type_name",
            "appointment_type_duration",
            "appointment_type_price",
            "appointment_date",
            "appointment_time",
            "status",
            "status_display",
            "reason",
            "created_at",
        ]
        read_only_fields = fields