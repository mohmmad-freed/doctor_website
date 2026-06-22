from rest_framework import serializers
from .models import DoctorAvailability, Specialty, DoctorProfile, DoctorSpecialty


class SpecialtySerializer(serializers.ModelSerializer):
    """Serializer for medical specialties."""

    doctor_count = serializers.SerializerMethodField()

    class Meta:
        model = Specialty
        fields = ["id", "name", "name_ar", "description", "doctor_count"]

    def get_doctor_count(self, obj):
        # Prefer a queryset annotation (set by the list view) to avoid a COUNT
        # query per specialty; fall back to a direct count for single-object use.
        count = getattr(obj, "doctor_count", None)
        return count if count is not None else obj.doctors.count()


class DoctorSpecialtySerializer(serializers.ModelSerializer):
    """Serializer for doctor-specialty through model."""

    id = serializers.IntegerField(source="specialty.id")
    name = serializers.CharField(source="specialty.name")
    name_ar = serializers.CharField(source="specialty.name_ar")

    class Meta:
        model = DoctorSpecialty
        fields = ["id", "name", "name_ar", "is_primary"]


class DoctorProfileListSerializer(serializers.ModelSerializer):
    """
    Serializer for doctor listing — used in browse/search views.
    Includes user info + specialties.
    """

    name = serializers.CharField(source="user.name")
    phone = serializers.CharField(source="user.phone")
    specialties = DoctorSpecialtySerializer(
        source="doctor_specialties", many=True, read_only=True
    )
    primary_specialty = serializers.SerializerMethodField()

    class Meta:
        model = DoctorProfile
        fields = [
            "id",
            "name",
            "phone",
            "bio",
            "years_of_experience",
            "specialties",
            "primary_specialty",
        ]

    def get_primary_specialty(self, obj):
        # Iterate the prefetched `doctor_specialties` (the list views prefetch
        # `doctor_specialties__specialty`) instead of `.filter(...)`, which would
        # ignore the prefetch cache and fire one query per doctor.
        primary = next(
            (ds for ds in obj.doctor_specialties.all() if ds.is_primary),
            None,
        )
        if primary:
            return {
                "id": primary.specialty.id,
                "name": primary.specialty.name,
                "name_ar": primary.specialty.name_ar,
            }
        return None


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