from rest_framework import serializers
from accounts.models import CustomUser
from .models import PatientProfile


class PatientProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for the Patient Profile.
    Combines data from the User model and the PatientProfile model.
    """

    # Fields from CustomUser
    name = serializers.CharField(source="user.name")
    phone = serializers.CharField(source="user.phone")
    email = serializers.EmailField(source="user.email")
    city = serializers.CharField(source="user.city.name", default=None)
    national_id = serializers.CharField(source="user.national_id")

    class Meta:
        model = PatientProfile
        fields = [
            "name",
            "phone",
            "email",
            "city",
            "national_id",
            "date_of_birth",
            "gender",
            "blood_type",
            "medical_history",
            "allergies",
            "emergency_contact_name",
            "emergency_contact_phone",
        ]
