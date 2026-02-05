from twilio.rest import Client
from django.conf import settings

client = Client(
    settings.TWILIO_ACCOUNT_SID,
    settings.TWILIO_AUTH_TOKEN
)


def send_otp(phone: str):
    return client.verify.services(
        settings.TWILIO_VERIFY_SID
    ).verifications.create(
        to=phone,
        channel="sms"
    )


def verify_otp(phone: str, code: str) -> bool:
    check = client.verify.services(
        settings.TWILIO_VERIFY_SID
    ).verification_checks.create(
        to=phone,
        code=code
    )
    return check.status == "approved"
