from rest_framework import serializers
from .models import DoctorAvailability


class DoctorAvailabilitySerializer(serializers.ModelSerializer):
    """Serializer for doctor weekly availability schedule."""

    day_name = serializers.CharField(source="get_day_of_week_display", read_only=True)
    clinic_name = serializers.CharField(source="clinic.name", read_only=True)

    class Meta:
        model = DoctorAvailability
        fields = [
            "id",
            "day_of_week",
            "day_name",
            "start_time",
            "end_time",
            "clinic",
            "clinic_name",
            "is_active",
        ]


class AvailableSlotSerializer(serializers.Serializer):
    """
    Serializer for computed time slots.
    These are not database records — they are generated on-the-fly
    from DoctorAvailability + existing Appointments.
    """

    time = serializers.TimeField(format="%H:%M")
    end_time = serializers.TimeField(format="%H:%M")
    is_available = serializers.BooleanField()