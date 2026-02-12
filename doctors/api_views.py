from datetime import date, datetime

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.models import AppointmentType
from appointments.serializers import AppointmentTypeSerializer
from .models import DoctorAvailability, Specialty, DoctorProfile
from .serializers import (
    DoctorAvailabilitySerializer,
    AvailableSlotSerializer,
    SpecialtySerializer,
    DoctorProfileListSerializer,
)
from .services import generate_slots_for_date


class SpecialtyListAPIView(APIView):
    """
    GET /api/doctors/specialties/

    Returns all specialties with doctor count.
    Accessible by authenticated patients.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        specialties = Specialty.objects.all()

        if not specialties.exists():
            return Response(
                {
                    "detail": "No specialties found.",
                    "results": [],
                },
                status=status.HTTP_200_OK,
            )

        serializer = SpecialtySerializer(specialties, many=True)
        return Response({"results": serializer.data}, status=status.HTTP_200_OK)


class DoctorsBySpecialtyAPIView(APIView):
    """
    GET /api/doctors/by-specialty/<specialty_id>/

    Returns doctors with the given specialty (primary or secondary).
    Accessible by authenticated patients.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, specialty_id):
        try:
            specialty = Specialty.objects.get(id=specialty_id)
        except Specialty.DoesNotExist:
            return Response(
                {"detail": "Specialty not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        doctor_profiles = DoctorProfile.objects.filter(
            specialties=specialty,
        ).select_related("user").prefetch_related(
            "doctor_specialties__specialty",
        )

        if not doctor_profiles.exists():
            return Response(
                {
                    "detail": f"No doctors found for {specialty.name_ar}.",
                    "specialty": SpecialtySerializer(specialty).data,
                    "results": [],
                },
                status=status.HTTP_200_OK,
            )

        serializer = DoctorProfileListSerializer(doctor_profiles, many=True)
        return Response(
            {
                "specialty": SpecialtySerializer(specialty).data,
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class DoctorListAPIView(APIView):
    """
    GET /api/doctors/
    GET /api/doctors/?specialty_id=X

    Returns all doctors, optionally filtered by specialty.
    Accessible by authenticated patients.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        specialty_id = request.query_params.get("specialty_id")

        doctor_profiles = DoctorProfile.objects.select_related(
            "user"
        ).prefetch_related("doctor_specialties__specialty")

        if specialty_id:
            doctor_profiles = doctor_profiles.filter(specialties__id=specialty_id)

        if not doctor_profiles.exists():
            return Response(
                {
                    "detail": "No doctors found.",
                    "results": [],
                },
                status=status.HTTP_200_OK,
            )

        serializer = DoctorProfileListSerializer(doctor_profiles.distinct(), many=True)
        return Response({"results": serializer.data}, status=status.HTTP_200_OK)


# --- Existing endpoints from Task 1 ---


class DoctorAvailabilityListAPIView(APIView):
    """
    GET /api/doctors/<doctor_id>/availability/?clinic_id=X

    Returns the recurring weekly schedule for a doctor at a specific clinic.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, doctor_id):
        clinic_id = request.query_params.get("clinic_id")
        if not clinic_id:
            return Response(
                {"detail": "clinic_id query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        availability = DoctorAvailability.objects.filter(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            is_active=True,
        )

        if not availability.exists():
            return Response(
                {
                    "detail": "No availability found for this doctor at this clinic.",
                    "results": [],
                },
                status=status.HTTP_200_OK,
            )

        serializer = DoctorAvailabilitySerializer(availability, many=True)
        return Response({"results": serializer.data}, status=status.HTTP_200_OK)


class DoctorAvailableSlotsAPIView(APIView):
    """
    GET /api/doctors/<doctor_id>/available-slots/?clinic_id=X&date=YYYY-MM-DD&appointment_type_id=Y

    Returns computed bookable time slots for a specific date.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, doctor_id):
        clinic_id = request.query_params.get("clinic_id")
        date_str = request.query_params.get("date")
        appointment_type_id = request.query_params.get("appointment_type_id")

        errors = {}
        if not clinic_id:
            errors["clinic_id"] = "This query parameter is required."
        if not date_str:
            errors["date"] = "This query parameter is required (format: YYYY-MM-DD)."
        if not appointment_type_id:
            errors["appointment_type_id"] = "This query parameter is required."

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"date": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if target_date < date.today():
            return Response(
                {"date": "Cannot view slots for past dates."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            appointment_type = AppointmentType.objects.get(
                id=appointment_type_id,
                doctor_id=doctor_id,
                clinic_id=clinic_id,
                is_active=True,
            )
        except AppointmentType.DoesNotExist:
            return Response(
                {"appointment_type_id": "Appointment type not found for this doctor and clinic."},
                status=status.HTTP_404_NOT_FOUND,
            )

        slots = generate_slots_for_date(
            doctor_id=doctor_id,
            clinic_id=int(clinic_id),
            target_date=target_date,
            duration_minutes=appointment_type.duration_minutes,
        )

        if not slots:
            return Response(
                {
                    "detail": "No availability on this date.",
                    "date": date_str,
                    "day_of_week": target_date.strftime("%A"),
                    "results": [],
                },
                status=status.HTTP_200_OK,
            )

        serializer = AvailableSlotSerializer(slots, many=True)
        return Response(
            {
                "date": date_str,
                "day_of_week": target_date.strftime("%A"),
                "doctor_id": doctor_id,
                "clinic_id": int(clinic_id),
                "appointment_type": appointment_type.name,
                "duration_minutes": appointment_type.duration_minutes,
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class DoctorAppointmentTypesAPIView(APIView):
    """
    GET /api/doctors/<doctor_id>/appointment-types/?clinic_id=X

    Returns appointment types offered by a doctor at a specific clinic.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, doctor_id):
        clinic_id = request.query_params.get("clinic_id")
        if not clinic_id:
            return Response(
                {"detail": "clinic_id query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        appointment_types = AppointmentType.objects.filter(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            is_active=True,
        )

        if not appointment_types.exists():
            return Response(
                {
                    "detail": "No appointment types found for this doctor at this clinic.",
                    "results": [],
                },
                status=status.HTTP_200_OK,
            )

        serializer = AppointmentTypeSerializer(appointment_types, many=True)
        return Response({"results": serializer.data}, status=status.HTTP_200_OK)