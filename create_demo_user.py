import os
import django
import sys

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clinic_website.settings")
django.setup()

from accounts.models import CustomUser
from patients.models import PatientProfile
from datetime import date


def create_demo_user():
    phone = "0598765432"
    password = "0598765432"
    name = "Demo Patient"

    # Check if user already exists
    if CustomUser.objects.filter(phone=phone).exists():
        print(f"User with phone {phone} already exists.")
        user = CustomUser.objects.get(phone=phone)
        # Reset password just in case
        user.set_password(password)
        user.save()
        print("Password updated.")
    else:
        print(f"Creating new user {name}...")
        user = CustomUser.objects.create_user(
            phone=phone,
            password=password,
            name=name,
            role="PATIENT",
            is_verified=False,  # Explicitly set as requested
        )
        print("User created.")

    # Check/Create Profile
    if not PatientProfile.objects.filter(user=user).exists():
        print("Creating patient profile...")
        PatientProfile.objects.create(
            user=user,
            date_of_birth=date(1990, 1, 1),
            gender="M",
            blood_type="O+",
            medical_history="None",
            allergies="None",
        )
        print("Profile created.")
    else:
        print("Patient profile already exists.")

    print(f"Done! Login with Phone: {phone} / Password: {password}")


if __name__ == "__main__":
    try:
        create_demo_user()
    except Exception as e:
        print(f"Error: {e}")
