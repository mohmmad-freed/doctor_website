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

User = get_user_model()

BASE_URL = "http://127.0.0.1:8000/api/login/"
PHONE = "0599999999"
PASSWORD = "testpassword123"


def setup_user(role="PATIENT", is_verified=False):
    # Delete if exists
    User.objects.filter(phone=PHONE).delete()

    # Create user
    user = User.objects.create_user(phone=PHONE, password=PASSWORD)
    user.name = "Test User"
    user.role = role
    user.is_verified = is_verified
    user.save()
    print(f"Created user: {PHONE}, Role: {role}, Verified: {is_verified}")
    return user


def test_login(description, expected_status, expected_error=None):
    print(f"\n--- Test: {description} ---")
    data = {"phone": PHONE, "password": PASSWORD}

    # Special case for wrong password test
    if "Wrong Password" in description:
        data["password"] = "wrongpassword"

    # Special case for non-existent user
    if "Non-existent" in description:
        data["phone"] = "0590000000"  # Random number

    try:
        response = requests.post(BASE_URL, data=data)
        print(f"Status Code: {response.status_code}")

        try:
            content = response.json()
            # print(f"Response: {content}")
        except:
            print(f"Response (text): {response.text}")
            content = {}

        if response.status_code == expected_status:
            if expected_error:
                error_msg = content.get("detail", "")
                if expected_error in str(error_msg):
                    print("✅ PASS: Correct status and error message.")
                else:
                    print(
                        f"❌ FAIL: Expected error '{expected_error}', got '{error_msg}'"
                    )
            else:
                if "access" in content:
                    print("✅ PASS: Login successful, token received.")
                else:
                    print("❌ FAIL: No token received.")
        else:
            print(
                f"❌ FAIL: Expected status {expected_status}, got {response.status_code}"
            )

    except Exception as e:
        print(f"❌ ERROR: {e}")


def run_tests():
    # 1. Test Non-existent User
    test_login("Non-existent User", 400, "No active account found")

    # 2. Test Unverified User
    setup_user(is_verified=False)
    test_login("Unverified User", 400, "Phone number is not verified")

    # 3. Test Wrong Password
    setup_user(is_verified=True)
    test_login("Wrong Password", 400, "Incorrect password")

    # 4. Test Wrong Role (Doctor)
    setup_user(role="DOCTOR", is_verified=True)
    test_login("Wrong Role (Doctor)", 400, "Access denied")

    # 5. Test Success (Verified Patient)
    setup_user(role="PATIENT", is_verified=True)
    test_login("Success (Verified Patient)", 200)

    # Cleanup
    User.objects.filter(phone=PHONE).delete()
    print("\nTests Completed.")


if __name__ == "__main__":
    run_tests()
