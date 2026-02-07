from django.core.management.base import BaseCommand
from accounts.models import City


class Command(BaseCommand):
    help = "Populates the database with initial Palestinian cities."

    def handle(self, *args, **kwargs):
        cities = [
            "الخليل",
            "نابلس",
            "رام الله",
            "القدس",
            "جنين",
            "طولكرم",
            "بيت لحم",
            "قلقيلية",
            "سلفيت",
            "أريحا",
            "طوباس",
            "غزة",
        ]

        for city_name in cities:
            city, created = City.objects.get_or_create(name=city_name)
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created city "{city_name}"'))
            else:
                self.stdout.write(
                    self.style.WARNING(f'City "{city_name}" already exists')
                )

        self.stdout.write(self.style.SUCCESS("Successfully populated cities."))
