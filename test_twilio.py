import os
import django
import sys
from django.conf import settings
from twilio.rest import Client

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clinic_website.settings")
django.setup()


def test_twilio_send():
    phone = (
        "+970598765432"  # Testing with Palestine country code and user's demo number
    )
    # Ideally we want a real number to test real SMS.
    # But let's first see if the CLIENT auth works at all.

    print(f"Testing Twilio Configuration...")
    print(f"Account SID: {settings.TWILIO_ACCOUNT_SID[:5]}...")
    print(f"Service SID: {settings.TWILIO_VERIFY_SID[:5]}...")

    try:
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

        # 1. Test Fetching Service (validates creds)
        service = client.verify.services(settings.TWILIO_VERIFY_SID).fetch()
        print(f"Service Found: {service.friendly_name}")

        # 2. Try sending (this might fail if number is invalid/unverified)
        print(f"Attempting to send OTP to {phone}...")
        verification = client.verify.services(
            settings.TWILIO_VERIFY_SID
        ).verifications.create(to=phone, channel="sms")
        print(f"Success! Status: {verification.status}")

    except Exception as e:
        print("\n[TWILIO ERROR]")
        print("-" * 30)
        print(e)
        print("-" * 30)


if __name__ == "__main__":
    test_twilio_send()
