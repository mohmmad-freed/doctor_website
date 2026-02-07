from django.db import migrations

CITIES = [
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


def populate_cities(apps, schema_editor):
    City = apps.get_model("accounts", "City")
    for city_name in CITIES:
        City.objects.get_or_create(name=city_name)


def reverse_populate_cities(apps, schema_editor):
    City = apps.get_model("accounts", "City")
    City.objects.filter(name__in=CITIES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(populate_cities, reverse_populate_cities),
    ]
