"""
Tests for the AI-scribe feature:
  • service: budget enforcement (free bypasses, paid blocked at cap), atomic
    spend recording, model-allowed validation, disabled config.
  • clinic-owner management view: authorization + save semantics.
  • doctor draft endpoint: access control, disabled handling, draft-only (no save).

OpenRouter is always mocked — no network calls, no key needed.
"""
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from clinics.models import ClinicStaff
from patients.models import ClinicPatient, ClinicalNote

from ai_scribe import services
from ai_scribe.models import (
    AIModel, AIUsageRecord, DoctorClinicAIConfig, DoctorMonthlySpend, current_period,
)
from doctors.test_views import DoctorViewTestBase


def _sections():
    return [{"type": "SUBJECTIVE", "label": "S", "name": "subjective", "elem_id": None, "value": ""}]


# ════════════════════════════════════════════════════════════════════
#  Service — budget enforcement
# ════════════════════════════════════════════════════════════════════

class AIScribeServiceTests(DoctorViewTestBase):

    def setUp(self):
        super().setUp()
        self.free = AIModel.objects.create(display_name="Free", openrouter_model_id="x/llm:free", is_free=True, sort_order=0)
        self.paid = AIModel.objects.create(display_name="Smart", openrouter_model_id="anthropic/claude", is_free=False, sort_order=1)
        self.cfg = DoctorClinicAIConfig.objects.create(
            clinic=self.clinic_a, doctor=self.doctor_a, is_enabled=True, monthly_limit_usd=Decimal("1.00"),
        )
        self.cfg.allowed_models.set([self.free, self.paid])

    @patch("ai_scribe.services._openrouter_chat")
    def test_paid_records_spend_atomically(self, mock_chat):
        mock_chat.return_value = ('{"subjective": "cough 3 days"}', 1000, 500, Decimal("0.05"))
        cfg = services.get_config(self.clinic_a.id, self.doctor_a)
        model = services.resolve_model(cfg, self.paid.id)
        values = services.draft_note_sections(config=cfg, model=model, sections=_sections(), transcript="cough")
        self.assertEqual(values["subjective"], "cough 3 days")
        spend = DoctorMonthlySpend.objects.get(clinic=self.clinic_a, doctor=self.doctor_a, period=current_period())
        self.assertEqual(spend.spent_usd, Decimal("0.05"))
        rec = AIUsageRecord.objects.get(doctor=self.doctor_a)
        self.assertEqual(rec.cost_usd, Decimal("0.05"))
        self.assertFalse(rec.was_free)

    @patch("ai_scribe.services._openrouter_chat")
    def test_paid_blocked_at_cap(self, mock_chat):
        DoctorMonthlySpend.objects.create(
            clinic=self.clinic_a, doctor=self.doctor_a, period=current_period(), spent_usd=Decimal("1.00"),
        )
        cfg = services.get_config(self.clinic_a.id, self.doctor_a)
        with self.assertRaises(services.BudgetExceeded):
            services.draft_note_sections(config=cfg, model=self.paid, sections=_sections(), transcript="x")
        mock_chat.assert_not_called()  # never reaches the API once over budget

    @patch("ai_scribe.services._openrouter_chat")
    def test_free_bypasses_cap_and_costs_zero(self, mock_chat):
        mock_chat.return_value = ('{"subjective": "ok"}', 10, 10, Decimal("0.99"))  # cost ignored for free
        DoctorMonthlySpend.objects.create(
            clinic=self.clinic_a, doctor=self.doctor_a, period=current_period(), spent_usd=Decimal("5.00"),
        )
        cfg = services.get_config(self.clinic_a.id, self.doctor_a)
        values = services.draft_note_sections(config=cfg, model=self.free, sections=_sections(), transcript="x")
        self.assertEqual(values["subjective"], "ok")
        spend = DoctorMonthlySpend.objects.get(clinic=self.clinic_a, doctor=self.doctor_a, period=current_period())
        self.assertEqual(spend.spent_usd, Decimal("5.00"))  # unchanged — free is free
        rec = AIUsageRecord.objects.filter(doctor=self.doctor_a).latest("created_at")
        self.assertTrue(rec.was_free)
        self.assertEqual(rec.cost_usd, Decimal("0"))

    def test_resolve_model_rejects_disallowed(self):
        other = AIModel.objects.create(display_name="Other", openrouter_model_id="x/o")
        cfg = services.get_config(self.clinic_a.id, self.doctor_a)
        with self.assertRaises(services.ModelNotAllowed):
            services.resolve_model(cfg, other.id)

    def test_disabled_config_raises(self):
        self.cfg.is_enabled = False
        self.cfg.save()
        cfg = services.get_config(self.clinic_a.id, self.doctor_a)  # None — only enabled configs returned
        self.assertIsNone(cfg)
        with self.assertRaises(services.AIScribeDisabled):
            services.draft_note_sections(config=cfg, model=self.free, sections=_sections(), transcript="x")


# ════════════════════════════════════════════════════════════════════
#  Clinic-owner management view
# ════════════════════════════════════════════════════════════════════

class OwnerAISettingsTests(DoctorViewTestBase):

    def setUp(self):
        super().setUp()
        self.free = AIModel.objects.create(display_name="Free", openrouter_model_id="x:free", is_free=True)
        self.paid = AIModel.objects.create(display_name="Smart", openrouter_model_id="x/smart")
        self.staff = ClinicStaff.objects.get(clinic=self.clinic_a, user=self.doctor_a)
        self.url = reverse("clinics:manage_doctor_ai", args=[self.clinic_a.id, self.staff.id])

    def test_non_owner_denied(self):
        # doctor_a is staff, NOT the clinic owner (main_doc_a is).
        self.client.force_login(self.doctor_a)
        resp = self.client.get(self.url)
        self.assertIn(resp.status_code, (302, 403, 404))

    def test_owner_get_renders(self):
        """The owner settings page renders end-to-end (template + context)."""
        AIModel.objects.create(display_name="Smart 2", openrouter_model_id="x/smart2")
        self.client.force_login(self.main_doc_a)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "model-row")          # styled model rows present
        self.assertContains(resp, self.free.display_name)

    def test_owner_can_save(self):
        self.client.force_login(self.main_doc_a)
        resp = self.client.post(self.url, {
            "is_enabled": "on", "monthly_limit_usd": "12.50",
            "allowed_models": [str(self.free.id), str(self.paid.id)],
        })
        self.assertEqual(resp.status_code, 302)
        cfg = DoctorClinicAIConfig.objects.get(clinic=self.clinic_a, doctor=self.doctor_a)
        self.assertTrue(cfg.is_enabled)
        self.assertEqual(cfg.monthly_limit_usd, Decimal("12.50"))
        self.assertEqual(set(cfg.allowed_models.values_list("id", flat=True)), {self.free.id, self.paid.id})

    def test_selected_model_cleared_when_unallowed(self):
        self.client.force_login(self.main_doc_a)
        cfg = DoctorClinicAIConfig.objects.create(
            clinic=self.clinic_a, doctor=self.doctor_a, is_enabled=True, selected_model=self.paid,
        )
        cfg.allowed_models.set([self.free, self.paid])
        self.client.post(self.url, {
            "is_enabled": "on", "monthly_limit_usd": "5", "allowed_models": [str(self.free.id)],
        })
        cfg.refresh_from_db()
        self.assertIsNone(cfg.selected_model_id)
        self.assertEqual(set(cfg.allowed_models.values_list("id", flat=True)), {self.free.id})


# ════════════════════════════════════════════════════════════════════
#  Doctor draft endpoint
# ════════════════════════════════════════════════════════════════════

class AIDraftEndpointTests(DoctorViewTestBase):

    def setUp(self):
        super().setUp()
        ClinicPatient.objects.get_or_create(patient=self.patient_a, clinic=self.clinic_a)
        self.free = AIModel.objects.create(display_name="Free", openrouter_model_id="x:free", is_free=True)
        self.cfg = DoctorClinicAIConfig.objects.create(
            clinic=self.clinic_a, doctor=self.doctor_a, is_enabled=True, monthly_limit_usd=Decimal("1"),
        )
        self.cfg.allowed_models.set([self.free])
        self.url = reverse("doctors:ws_note_ai_draft", args=[self.patient_a.id])

    @patch("ai_scribe.services._openrouter_chat")
    def test_draft_prefills_form_without_saving(self, mock_chat):
        mock_chat.return_value = ('{"subjective": "headache"}', 100, 50, Decimal("0"))
        self.client.force_login(self.doctor_a)
        resp = self.client.post(
            self.url,
            {"clinic_id": self.clinic_a.id, "ai_model_id": self.free.id, "transcript": "patient reports headache"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["ai_drafted"])
        self.assertTrue(resp.context["note_form_open"])
        values = [s["value"] for s in resp.context["active_note_sections"]]
        self.assertIn("headache", values)
        # Draft only — nothing persisted until the doctor saves the form.
        self.assertEqual(ClinicalNote.objects.filter(patient=self.patient_a).count(), 0)

    def test_disabled_shows_error(self):
        self.cfg.is_enabled = False
        self.cfg.save()
        self.client.force_login(self.doctor_a)
        resp = self.client.post(
            self.url, {"clinic_id": self.clinic_a.id, "transcript": "x"}, HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context.get("ai_error"))

    def test_idor_forbidden(self):
        # doctor_b shares no clinic with patient_a → access denied.
        self.client.force_login(self.doctor_b)
        resp = self.client.post(
            self.url, {"clinic_id": self.clinic_a.id, "transcript": "x"}, HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 403)


# ════════════════════════════════════════════════════════════════════
#  Phase 2 — speech-to-text service
# ════════════════════════════════════════════════════════════════════

class STTServiceTests(DoctorViewTestBase):

    def setUp(self):
        super().setUp()
        self.free = AIModel.objects.create(display_name="Free", openrouter_model_id="x:free", is_free=True)
        self.cfg = DoctorClinicAIConfig.objects.create(
            clinic=self.clinic_a, doctor=self.doctor_a, is_enabled=True, monthly_limit_usd=Decimal("1.00"),
        )
        self.cfg.allowed_models.set([self.free])

    @override_settings(STT_PROVIDER="openai", STT_API_KEY="k", STT_PRICE_PER_MINUTE="0")
    @patch("ai_scribe.services._stt_transcribe")
    def test_free_stt_costs_zero(self, mock_stt):
        mock_stt.return_value = ("hello world", 30.0, None)
        cfg = services.get_config(self.clinic_a.id, self.doctor_a)
        text, cost, dur = services.transcribe_audio(
            config=cfg, file_obj=b"x", filename="a.webm", content_type="audio/webm",
        )
        self.assertEqual(text, "hello world")
        self.assertEqual(cost, Decimal("0"))
        rec = AIUsageRecord.objects.filter(doctor=self.doctor_a).latest("created_at")
        self.assertTrue(rec.was_free)
        self.assertIn("STT", rec.model_label)

    @override_settings(STT_PROVIDER="openai", STT_API_KEY="k", STT_PRICE_PER_MINUTE="0.006")
    @patch("ai_scribe.services._stt_transcribe")
    def test_paid_stt_meters_cost(self, mock_stt):
        mock_stt.return_value = ("text", 120.0, None)  # 2 minutes
        cfg = services.get_config(self.clinic_a.id, self.doctor_a)
        text, cost, dur = services.transcribe_audio(
            config=cfg, file_obj=b"x", filename="a.webm", content_type="audio/webm",
        )
        self.assertEqual(cost, Decimal("0.012"))  # 2 min × $0.006
        spend = DoctorMonthlySpend.objects.get(clinic=self.clinic_a, doctor=self.doctor_a, period=current_period())
        self.assertEqual(spend.spent_usd, Decimal("0.012"))

    @override_settings(STT_PROVIDER="openai", STT_API_KEY="k", STT_PRICE_PER_MINUTE="0.10")
    @patch("ai_scribe.services._stt_transcribe")
    def test_paid_stt_blocked_over_budget(self, mock_stt):
        DoctorMonthlySpend.objects.create(
            clinic=self.clinic_a, doctor=self.doctor_a, period=current_period(), spent_usd=Decimal("1.00"),
        )
        cfg = services.get_config(self.clinic_a.id, self.doctor_a)
        with self.assertRaises(services.BudgetExceeded):
            services.transcribe_audio(config=cfg, file_obj=b"x", filename="a.webm", content_type="audio/webm")
        mock_stt.assert_not_called()

    @override_settings(STT_PROVIDER="openrouter", OPENROUTER_API_KEY="k")
    @patch("ai_scribe.services._stt_transcribe")
    def test_openrouter_uses_response_cost(self, mock_stt):
        """OpenRouter mode reuses the OpenRouter key and meters its returned cost."""
        mock_stt.return_value = ("transcript", 60.0, Decimal("0.02"))
        cfg = services.get_config(self.clinic_a.id, self.doctor_a)
        text, cost, dur = services.transcribe_audio(
            config=cfg, file_obj=b"x", filename="a.webm", content_type="audio/webm",
        )
        self.assertEqual(text, "transcript")
        self.assertEqual(cost, Decimal("0.02"))
        spend = DoctorMonthlySpend.objects.get(clinic=self.clinic_a, doctor=self.doctor_a, period=current_period())
        self.assertEqual(spend.spent_usd, Decimal("0.02"))


# ════════════════════════════════════════════════════════════════════
#  Phase 2 — transcribe endpoint
# ════════════════════════════════════════════════════════════════════

class STTEndpointTests(DoctorViewTestBase):

    def setUp(self):
        super().setUp()
        ClinicPatient.objects.get_or_create(patient=self.patient_a, clinic=self.clinic_a)
        self.free = AIModel.objects.create(display_name="Free", openrouter_model_id="x:free", is_free=True)
        self.cfg = DoctorClinicAIConfig.objects.create(
            clinic=self.clinic_a, doctor=self.doctor_a, is_enabled=True, monthly_limit_usd=Decimal("1"),
        )
        self.cfg.allowed_models.set([self.free])
        self.url = reverse("doctors:ws_note_ai_transcribe", args=[self.patient_a.id])

    def _audio(self):
        return SimpleUploadedFile("rec.webm", b"\x1a\x45\xdf\xa3fakeaudio", content_type="audio/webm")

    @override_settings(STT_PROVIDER="openai", STT_API_KEY="")
    def test_not_configured_returns_503(self):
        self.client.force_login(self.doctor_a)
        resp = self.client.post(self.url, {"clinic_id": self.clinic_a.id, "audio": self._audio()})
        self.assertEqual(resp.status_code, 503)

    @override_settings(STT_PROVIDER="openai", STT_API_KEY="test-key", STT_PRICE_PER_MINUTE="0")
    @patch("ai_scribe.services._stt_transcribe")
    def test_transcribes_audio(self, mock_stt):
        mock_stt.return_value = ("patient has a cough", 12.0, None)
        self.client.force_login(self.doctor_a)
        resp = self.client.post(self.url, {"clinic_id": self.clinic_a.id, "audio": self._audio()})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["text"], "patient has a cough")

    @override_settings(STT_PROVIDER="openai", STT_API_KEY="test-key")
    def test_missing_audio_returns_400(self):
        self.client.force_login(self.doctor_a)
        resp = self.client.post(self.url, {"clinic_id": self.clinic_a.id})
        self.assertEqual(resp.status_code, 400)

    @override_settings(STT_PROVIDER="openai", STT_API_KEY="test-key")
    def test_idor_forbidden(self):
        self.client.force_login(self.doctor_b)
        resp = self.client.post(self.url, {"clinic_id": self.clinic_a.id, "audio": self._audio()})
        self.assertEqual(resp.status_code, 403)
