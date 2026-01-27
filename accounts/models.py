from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
import uuid
from django.conf import settings

class CustomUserManager(BaseUserManager):
    """Custom user manager where email is the unique identifier"""
    
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        
        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    """Custom user model with email login and role-based access"""
    
    ROLE_CHOICES = [
        ('PATIENT', 'Patient'),
        ('MAIN_DOCTOR', 'Main Doctor'),
        ('DOCTOR', 'Doctor'),
        ('SECRETARY', 'Secretary'),
    ]
    
    # Remove username, use email instead
    username = None
    email = models.EmailField(unique=True)
    
    # Additional fields
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    
    # Set email as the unique identifier
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name', 'role']  # Required when creating superuser
    
    objects = CustomUserManager()
    
    def __str__(self):
        return f"{self.name} ({self.email}) - {self.role}"
    



