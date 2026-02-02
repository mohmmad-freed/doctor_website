from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
import re

User = get_user_model()


class PhoneNumberAuthBackend(ModelBackend):
    """
    Custom authentication backend that allows login with phone number
    Supports these formats:
        - 059XXXXXXX
        - 056XXXXXXX
        - +97059XXXXXXX
        - +97056XXXXXXX
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
        Normalize phone to 059XXXXXXX or 056XXXXXXX format
        Accepts: 059XXXXXXX, 056XXXXXXX, +97059XXXXXXX, +97056XXXXXXX
        Returns: 059XXXXXXX or 056XXXXXXX
        """
        if not phone:
            return phone

        phone = phone.strip().replace(" ", "").replace("-", "")

        # If starts with +97059 or +97056, convert to 059/056
        if phone.startswith("+97059"):
            phone = "059" + phone[6:]
        elif phone.startswith("+97056"):
            phone = "056" + phone[6:]

        return phone

    @staticmethod
    def is_valid_phone_number(phone):
        """
        Validate Palestinian phone number:
        Must start with 059 or 056 and have 10 digits total
        """
        return bool(re.match(r"^(059|056)\d{7}$", phone))
