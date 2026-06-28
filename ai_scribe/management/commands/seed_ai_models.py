"""
Seed example AI models for the AI scribe so you can add + test quickly.

Idempotent (keyed by openrouter_model_id). The free model is Active so you can
test end-to-end at $0; the paid example is left INACTIVE so it can't be used
until you verify its OpenRouter slug and activate it in Django admin.

    python manage.py seed_ai_models

Then: Django admin → AI Scribe → AI Models (verify slugs / activate), and the
clinic owner enables a doctor at /clinics/<id>/staff/<staff_id>/ai-settings/.
"""
from django.core.management.base import BaseCommand

from ai_scribe.models import AIModel

SEED = [
    {
        "display_name": "Free",
        "openrouter_model_id": "meta-llama/llama-3.3-70b-instruct:free",
        "is_free": True,
        "is_active": True,
        "sort_order": 0,
        "description": "No-cost model — never counts against a doctor's budget. Great for testing.",
    },
    {
        "display_name": "Balanced (Claude Sonnet)",
        "openrouter_model_id": "anthropic/claude-sonnet-4.6",
        "is_free": False,
        "is_active": False,  # verify the slug + activate in admin before use
        "sort_order": 10,
        "input_price_per_mtok": "3",
        "output_price_per_mtok": "15",
        "description": "Paid example — verify the OpenRouter slug at openrouter.ai/models, then set Active.",
    },
]


class Command(BaseCommand):
    help = "Seed example AI models (idempotent). Review/activate them in Django admin."

    def handle(self, *args, **options):
        for row in SEED:
            obj, created = AIModel.objects.get_or_create(
                openrouter_model_id=row["openrouter_model_id"],
                defaults=row,
            )
            prefix = self.style.SUCCESS("created") if created else self.style.WARNING("exists ")
            self.stdout.write(f"{prefix}  {obj}")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            "Done. Next: (1) set OPENROUTER_API_KEY in .env, (2) verify each slug at "
            "https://openrouter.ai/models and activate paid models in Django admin → AI Models, "
            "(3) as a clinic owner, click the AI button on a doctor's card to enable + set a budget."
        ))
