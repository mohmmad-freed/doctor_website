from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.urls import reverse
from accounts.models import CustomUser
from patients.models import PatientProfile


class PatientProfileAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.patient_user = CustomUser.objects.create_user(
            phone="1234567890",
            password="password123",
            role="PATIENT",
            name="Test Patient",
            national_id="1234567890",
        )
        self.patient_profile = PatientProfile.objects.create(
            user=self.patient_user, date_of_birth="2000-01-01", gender="M"
        )

        self.doctor_user = CustomUser.objects.create_user(
            phone="0987654321",
            password="password123",
            role="DOCTOR",
            name="Test Doctor",
        )
        # Assuming the URL name provided in urls.py is 'patient_profile_api'
        self.url = reverse("patient_profile_api")

    def test_get_profile_authenticated_patient(self):
        self.client.force_authenticate(user=self.patient_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check CustomUser fields
        self.assertEqual(response.data["name"], "Test Patient")
        self.assertEqual(response.data["phone"], "1234567890")
        self.assertEqual(response.data["national_id"], "1234567890")
        # Check PatientProfile fields
        self.assertEqual(response.data["gender"], "M")
        self.assertEqual(response.data["date_of_birth"], "2000-01-01")

    def test_get_profile_unauthenticated(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_profile_wrong_role(self):
        self.client.force_authenticate(user=self.doctor_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
