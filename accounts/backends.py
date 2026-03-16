from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
import re

User = get_user_model()


class PhoneNumberAuthBackend(ModelBackend):
    """
    Custom authentication backend that allows login with phone number
    Supports these formats:
        - 05XXXXXXXX (e.g., 059, 056, 050, 052...)
        - +9705XXXXXXXX
        - +9725XXXXXXXX (also common in the region)
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get("phone")

        if username is None or password is None:
            return None

        # Normalize the phone number
        normalized_phone = self.normalize_phone_number(username)

        # Validate format
        if not self.is_valid_phone_number(normalized_phone):
            return None

        try:
            # Try to get user with normalized phone number
            user = User.objects.get(phone=normalized_phone)

            # Check password
            if user.check_password(password):
                return user
        except User.DoesNotExist:
            # Run the default password hasher once to reduce timing differences
            User().set_password(password)
            return None

        return None

    @staticmethod
    def normalize_phone_number(phone):
        """
        Normalize phone to 05XXXXXXXX format
        Accepts: 05XXXXXXXX, +9705XXXXXXXX, +9725XXXXXXXX
        Returns: 05XXXXXXXX
        """
        if not phone:
            return phone

        phone = phone.strip().replace(" ", "").replace("-", "")

        # If starts with +9705... or +9725..., convert to 05...
        if phone.startswith("+9705"):
            phone = "05" + phone[5:]
        elif phone.startswith("+9725"):
            phone = "05" + phone[5:]

        return phone

    @staticmethod
    def is_valid_phone_number(phone):
        """
        Validate phone number:
        Must start with 05 and have 10 digits total.
        """
        return bool(re.match(r"^05\d{8}$", phone))
