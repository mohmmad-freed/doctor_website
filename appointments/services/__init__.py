# appointments/services package
#
# This package replaces the former flat appointments/services.py module.
# All symbols from both sub-modules are re-exported here so that existing
# imports continue to work without modification:
#
#   from appointments.services import BookingError, book_appointment
#   from appointments.services import get_patient_appointments
#   from appointments.services import cancel_appointment_by_staff

from appointments.services.booking_service import (  # noqa: F401
    BookingError,
    SlotUnavailableError,
    InvalidSlotError,
    PastDateError,
    book_appointment,
)

from appointments.services.patient_appointments_service import (  # noqa: F401
    cancel_appointment,
    cancel_appointment_by_staff,
    edit_appointment,
    get_patient_appointments,
    CANCELLATION_WINDOW_HOURS,
)