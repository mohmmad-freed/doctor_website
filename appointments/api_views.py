from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import BookAppointmentSerializer, AppointmentResponseSerializer
from .services import (
    book_appointment,
    BookingError,
    SlotUnavailableError,
    InvalidSlotError,
    PastDateError,
)


class BookAppointmentAPIView(APIView):
    """
    POST /appointments/api/book/

    Book an appointment as a patient.

    Request body:
        {
            "doctor_id": 5,
            "clinic_id": 1,
            "appointment_type_id": 3,
            "appointment_date": "2026-02-20",
            "appointment_time": "10:00",
            "reason": "Annual checkup"  (optional)
        }

    Success Response (201):
        Full appointment details via AppointmentResponseSerializer.

    Error Responses:
        400: Validation errors or booking errors.
        409: Slot no longer available (race condition).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Ensure only patients can book
        if getattr(request.user, "role", None) != "PATIENT":
            return Response(
                {"detail": "Only patients can book appointments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = BookAppointmentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            appointment = book_appointment(
                patient=request.user,
                doctor_id=serializer.validated_data["doctor_id"],
                clinic_id=serializer.validated_data["clinic_id"],
                appointment_type_id=serializer.validated_data["appointment_type_id"],
                appointment_date=serializer.validated_data["appointment_date"],
                appointment_time=serializer.validated_data["appointment_time"],
                reason=serializer.validated_data.get("reason", ""),
            )
        except SlotUnavailableError as e:
            return Response(
                {"detail": e.message, "code": e.code},
                status=status.HTTP_409_CONFLICT,
            )
        except (InvalidSlotError, PastDateError) as e:
            return Response(
                {"detail": e.message, "code": e.code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except BookingError as e:
            return Response(
                {"detail": e.message, "code": e.code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_serializer = AppointmentResponseSerializer(appointment)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)