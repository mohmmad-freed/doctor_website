import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = "Creates a super administrative user non-interactively if it doesn't exist"

    def handle(self, *args, **options):
        User = get_user_model()
        phone = os.environ.get("DJANGO_SUPERUSER_PHONE")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        name = os.environ.get("DJANGO_SUPERUSER_NAME", "Super Admin")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")

        if not phone or not password:
            self.stdout.write(
                self.style.ERROR(
                    "Missing DJANGO_SUPERUSER_PHONE or DJANGO_SUPERUSER_PASSWORD environment variables."
                )
            )
            return

        if User.objects.filter(phone=phone).exists():
            user = User.objects.get(phone=phone)
            # Ensure the existing user has superuser privileges
            if not user.is_superuser or not user.is_staff:
                user.is_superuser = True
                user.is_staff = True
                user.role = "MAIN_DOCTOR"  # As per CustomUserManager definition
                user.save()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"User '{phone}' already exists. Granted superuser privileges."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f"Superuser '{phone}' already exists.")
                )
        else:
            User.objects.create_superuser(
                phone=phone,
                password=password,
                name=name,
                email=email,
            )
            self.stdout.write(
                self.style.SUCCESS(f"Successfully created superuser '{phone}'.")
            )
