from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth import authenticate, get_user_model
from rest_framework import serializers
from django.conf import settings

User = get_user_model()


class LoginSerializer(TokenObtainPairSerializer):
    """
    Custom JWT Serializer to login with phone number.
    It leverages the backend's phone normalization logic.
    """

    # We can use the default fields (username, password), ensuring the frontend sends 'username': 'PHONE_NUMBER'
    # Or explicitly define phone field if we want to be stricter.

    def validate(self, attrs):
        # We expect 'username' (which is the phone) and 'password'
        # The frontend should send 'username': '059XXXXXXX'

        # 1. Normalize phone (backend logic handles this, but let's be explicit if needed)
        # However, SimpleJWT calls authenticate() which calls our backend.
        # But to control error messages GRANULARLY, we might need to do manual checks BEFORE authenticate,
        # or handle the failure after.

        # Let's extract credentials
        phone = attrs.get("username") or attrs.get("phone")
        password = attrs.get("password")

        if not phone or not password:
            raise serializers.ValidationError('Must include "phone" and "password".')

        # 1. Check if user exists (Pre-check for granular error)
        # We need to normalize first to match DB
        from accounts.backends import PhoneNumberAuthBackend

        normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(phone)

        try:
            user = User.objects.get(phone=normalized_phone)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {"detail": "No active account found with the given credentials"}
            )

        # 2. Check Verification (if enforced)
        if settings.ENFORCE_PHONE_VERIFICATION:
            if not getattr(
                user, "is_verified", True
            ):  # Default to True if field missing (safety), but we just added it.
                raise serializers.ValidationError(
                    {"detail": "Phone number is not verified."}
                )

        # 3. Check Role (API is strict on Patient)
        if user.role != "PATIENT":
            raise serializers.ValidationError(
                {"detail": "Access denied. Patients only."}
            )

        # 4. Check Password
        if not user.check_password(password):
            raise serializers.ValidationError({"detail": "Incorrect password."})

        # If all pass, we let SimpleJWT do its thing to generate tokens
        # Or we can just return the tokens manually if we want to skip re-authentication overhead,
        # but calling super().validate() is standard.
        # Since we pre-validated, super().validate() will succeed.

        # Ensure 'username' is set to the normalized phone for SimpleJWT
        attrs["username"] = normalized_phone

        return super().validate(attrs)

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Add custom claims
        token["name"] = user.name
        token["role"] = user.role
        return token
