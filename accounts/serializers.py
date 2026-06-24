from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth import authenticate, get_user_model
from rest_framework import serializers
from django.conf import settings

from accounts import ratelimit

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

        # Brute-force throttle — shares the "login" scope with the web login so
        # an attacker can't get a fresh budget by switching to the API. Two
        # layers: per-phone and per-IP. Checked before any password work so
        # guessing can't continue past the limit. Fail-open (Redis down → allow).
        ip = ratelimit.client_ip(self.context.get("request"))
        if ratelimit.is_blocked(
            "login", normalized_phone, ratelimit.LOGIN_MAX_ATTEMPTS
        ) or ratelimit.is_blocked("login_ip", ip, ratelimit.LOGIN_IP_MAX_ATTEMPTS):
            raise serializers.ValidationError(
                {"detail": "Too many failed attempts. Please try again later."}
            )

        def _register_failure():
            ratelimit.register_failure(
                "login", normalized_phone,
                ratelimit.LOGIN_WINDOW_SECONDS, limit=ratelimit.LOGIN_MAX_ATTEMPTS,
            )
            ratelimit.register_failure(
                "login_ip", ip,
                ratelimit.LOGIN_IP_WINDOW_SECONDS, limit=ratelimit.LOGIN_IP_MAX_ATTEMPTS,
            )

        # One generic credentials error for BOTH unknown phone and wrong
        # password, so the response never discloses whether an account exists
        # (matches the web login_view's non-enumeration posture).
        invalid_credentials = serializers.ValidationError(
            {"detail": "No active account found with the given credentials"}
        )

        # 1. Resolve the account. On miss, run a dummy hash so the response time
        # doesn't reveal whether the phone is registered, then fail generically.
        try:
            user = User.objects.get(phone=normalized_phone)
        except User.DoesNotExist:
            User().set_password(password)  # flatten timing vs. the real check below
            _register_failure()
            raise invalid_credentials

        # 2. Check the password BEFORE anything that would reveal account state.
        if not user.check_password(password):
            _register_failure()
            raise invalid_credentials

        # Password proven correct — reset the throttle counters for this phone.
        ratelimit.clear_failures("login", normalized_phone)

        # 3. Only now that the password is proven correct is it safe to disclose
        # verification/role state (an attacker without the password can't reach
        # these branches, so they can't be used for enumeration).
        if settings.ENFORCE_PHONE_VERIFICATION and not getattr(
            user, "is_verified", True
        ):
            raise serializers.ValidationError(
                {"detail": "Phone number is not verified."}
            )

        if not user.has_role("PATIENT"):
            raise serializers.ValidationError(
                {"detail": "Access denied. Patients only."}
            )

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
        token["roles"] = user.roles
        return token
