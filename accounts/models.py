from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.conf import settings
from django.db.models import Q


class City(models.Model):
    """Cities available in the system"""

    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Cities"
        ordering = ["name"]


class CustomUserManager(BaseUserManager):
    """Custom user manager where phone is the unique identifier"""

    def create_user(self, phone, password=None, **extra_fields):
        if not phone:
            raise ValueError("The Phone field must be set")
        user = self.model(phone=phone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("role", "MAIN_DOCTOR")

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(phone, password, **extra_fields)


class CustomUser(AbstractUser):
    """Custom user model with email login and role-based access"""

    ROLE_CHOICES = [
        ("PATIENT", "Patient"),
        ("MAIN_DOCTOR", "Main Doctor"),
        ("DOCTOR", "Doctor"),
        ("SECRETARY", "Secretary"),
    ]

    # Remove username, use email instead
    username = None
    email = models.EmailField(blank=True, null=True)  # Made optional for patients

    # Required fields for all users
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, unique=True)  # Required and unique
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="PATIENT")
    is_verified = models.BooleanField(
        default=False,
        help_text="Designates whether the user has verified their phone number.",
    )

    # Patient-specific fields (will be NULL for non-patients)
    national_id = models.CharField(max_length=20, unique=True, null=True, blank=True)
    city = models.ForeignKey("City", on_delete=models.SET_NULL, null=True, blank=True)

    # Set phone as the unique identifier for login
    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def __str__(self):
        return f"{self.name} ({self.phone}) - {self.role}"

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
