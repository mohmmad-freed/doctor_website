import os
import sys
import django
from django.conf import settings
from django.template.loader import render_to_string
from django.http import HttpRequest

# Determine project root and add to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clinic_website.settings")
django.setup()

from accounts.forms import PatientRegistrationForm


def reproduce():
    print("Instantiating form...")
    form = PatientRegistrationForm(initial={"phone": "0599000000"})

    context = {
        "form": form,
        "verified_email": "test@example.com",
        "email_is_verified": True,
        "original_email": "test@example.com",
    }

    print("Rendering template...")
    try:
        # Mock request
        request = HttpRequest()
        render_to_string(
            "accounts/register_patient_details.html", context, request=request
        )
        print("Template rendered successfully.")
    except Exception as e:
        print("Template rendering failed!")
        print(e)
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    reproduce()
