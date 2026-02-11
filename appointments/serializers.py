from rest_framework import serializers
from .models import AppointmentType


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