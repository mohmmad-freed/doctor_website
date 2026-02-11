from datetime import date, datetime

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.models import AppointmentType
from appointments.serializers import AppointmentTypeSerializer
from .models import DoctorAvailability
from .serializers import DoctorAvailabilitySerializer, AvailableSlotSerializer
from .services import generate_slots_for_date


class DoctorAvailabilityListAPIView(APIView):
    """
    GET /api/doctors/<doctor_id>/availability/?clinic_id=X

    Returns the recurring weekly schedule for a doctor at a specific clinic.
    Accessible by authenticated patients.
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
    Slots are generated from DoctorAvailability and filtered against
    existing appointments across ALL clinics (R-03).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, doctor_id):
        # --- Validate required params ---
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

        # --- Parse date ---
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"date": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Don't allow past dates
        if target_date < date.today():
            return Response(
                {"date": "Cannot view slots for past dates."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- Validate appointment type ---
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

        # --- Generate slots ---
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
    Accessible by authenticated patients.
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