import os
import django
import sys
import requests
import json

# Setup Django environment
sys.path.append("f:\\ملفات الc\\buisniss\\clink app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clinic_website.settings")
django.setup()

from django.contrib.auth import get_user_model
from patients.models import PatientProfile

User = get_user_model()

BASE_URL = "http://127.0.0.1:8000"
LOGIN_URL = f"{BASE_URL}/api/login/"
PROFILE_URL = f"{BASE_URL}/api/patient/profile/"

PHONE = "0598888888"
PASSWORD = "testpassword123"


def setup_data():
    # Cleanup previous run
    User.objects.filter(phone=PHONE).delete()

    # Create User
    user = User.objects.create_user(phone=PHONE, password=PASSWORD)
    user.name = "Test Patient API"
    user.role = "PATIENT"
    user.is_verified = True
    user.save()

    # Create Profile
    PatientProfile.objects.create(
        user=user, date_of_birth="1990-01-01", gender="M", blood_type="O+"
    )
    print(f"Created Patient: {PHONE}, Name: {user.name}")
    return user


def run_test():
    try:
        # 1. Login
        print("\n--- 1. Logging in ---")
        login_resp = requests.post(
            LOGIN_URL, data={"phone": PHONE, "password": PASSWORD}
        )

        if login_resp.status_code != 200:
            print(f"❌ Login Failed: {login_resp.status_code} {login_resp.text}")
            return

        token = login_resp.json().get("access")
        refresh = login_resp.json().get("refresh")
        print("✅ Login Successful, Token received.")

        # 2. Get Profile
        print("\n--- 2. Fetching Profile ---")
        headers = {"Authorization": f"Bearer {token}"}
        profile_resp = requests.get(PROFILE_URL, headers=headers)

        if profile_resp.status_code == 200:
            data = profile_resp.json()
            print("✅ Profile Fetched Successfully")
            print(json.dumps(data, indent=2))

            # Simple assertions
            if data["name"] == "Test Patient API" and data["gender"] == "M":
                print("✅ Data Verification Passed")
            else:
                print("❌ Data Verification Failed: Mismatch in expected values.")
        else:
            print(
                f"❌ Failed to fetch profile: {profile_resp.status_code} {profile_resp.text}"
            )

        # 3. Test Unauthenticated
        print("\n--- 3. Testing Unauthenticated Access ---")
        unauth_resp = requests.get(PROFILE_URL)
        if unauth_resp.status_code == 401:
            print("✅ Unauthenticated access correctly blocked (401)")
        else:
            print(
                f"❌ Unexpected status for unauthenticated: {unauth_resp.status_code}"
            )

    except Exception as e:
        print(f"❌ Error during test: {e}")
    finally:
        # Cleanup
        print("\n--- Cleanup ---")
        User.objects.filter(phone=PHONE).delete()
        print("Test User Deleted.")


if __name__ == "__main__":
    setup_data()
    run_test()
