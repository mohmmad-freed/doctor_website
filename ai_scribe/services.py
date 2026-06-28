"""
AI-scribe service layer: OpenRouter calls + budget enforcement.

Security model:
  • The OpenRouter API key is read from settings (env) and used only here,
    server-side. It is never sent to the browser.
  • Budget, clinic, model and doctor are always resolved server-side from the
    caller's session — never trusted from request bodies. Cost is taken from
    OpenRouter's response, not from the client.
  • Free models bypass the budget; paid models are blocked once the calendar-
    month spend reaches the cap. Spend is incremented atomically.
  • No transcript or generated note text is persisted (PHI stays out of the DB).
"""
import json
import logging
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import transaction

from .models import (
    AIModel,
    AIUsageRecord,
    DoctorClinicAIConfig,
    DoctorMonthlySpend,
    current_period,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


# ── Errors (the message in args[0] is safe to show the user) ──────────────────
class AIScribeError(Exception):
    pass


class AIScribeDisabled(AIScribeError):
    pass


class BudgetExceeded(AIScribeError):
    pass


class ModelNotAllowed(AIScribeError):
    pass


class OpenRouterError(AIScribeError):
    pass


class STTError(AIScribeError):
    pass


# ── Config / budget helpers ───────────────────────────────────────────────────
def get_config(clinic_id, doctor):
    """Return the ENABLED config for (clinic, doctor), or None."""
    return (
        DoctorClinicAIConfig.objects
        .filter(clinic_id=clinic_id, doctor=doctor, is_enabled=True)
        .select_related("selected_model")
        .prefetch_related("allowed_models")
        .first()
    )


def available_models(config):
    """Active models the doctor may choose from (allowed ∩ active)."""
    if config is None:
        return []
    return [m for m in config.allowed_models.all() if m.is_active]


def month_spend(clinic_id, doctor):
    row = DoctorMonthlySpend.objects.filter(
        clinic_id=clinic_id, doctor=doctor, period=current_period()
    ).first()
    return row.spent_usd if row else ZERO


def remaining_budget(config):
    if config is None:
        return ZERO
    rem = Decimal(config.monthly_limit_usd) - month_spend(config.clinic_id, config.doctor)
    return rem if rem > ZERO else ZERO


def resolve_model(config, model_id=None):
    """Pick the model to use: the requested one (must be allowed), else the
    doctor's saved choice, else a free model, else the first allowed model."""
    avail = available_models(config)
    by_id = {m.id: m for m in avail}
    if model_id not in (None, ""):
        try:
            mid = int(model_id)
        except (TypeError, ValueError):
            mid = None
        if mid in by_id:
            return by_id[mid]
        raise ModelNotAllowed("That model isn't available to you.")
    if config and config.selected_model_id in by_id:
        return by_id[config.selected_model_id]
    for m in avail:
        if m.is_free:
            return m
    return avail[0] if avail else None


# ── Drafting ──────────────────────────────────────────────────────────────────
def draft_note_sections(*, config, model, sections, transcript):
    """Fill ``sections`` from ``transcript`` via OpenRouter.

    Enforces the budget, records usage atomically, and returns
    ``{section_name: value}`` (only sections the model populated). Raises an
    ``AIScribeError`` subclass on any problem (caller shows ``.args[0]``).
    """
    if config is None or not config.is_enabled:
        raise AIScribeDisabled("AI scribe isn't enabled for you at this clinic.")
    if model is None:
        raise ModelNotAllowed("No AI model is available to you — ask your clinic admin.")
    if not (transcript or "").strip():
        raise AIScribeError("Add some dictation or notes first.")

    # Budget gate — free models always allowed; paid blocked at/over the cap.
    if not model.is_free and remaining_budget(config) <= ZERO:
        raise BudgetExceeded(
            "Your monthly AI budget is used up. Switch to the Free model, or ask "
            "your clinic admin to raise the limit."
        )

    system_prompt, user_prompt = _build_prompts(sections, transcript)
    text, in_tok, out_tok, cost = _openrouter_chat(
        model_id=model.openrouter_model_id,
        system=system_prompt,
        user=user_prompt,
        price_hint=(model.input_price_per_mtok, model.output_price_per_mtok),
    )
    if model.is_free:
        cost = ZERO

    values = _parse_sections(text, sections)
    _record_usage(config=config, model=model, in_tok=in_tok, out_tok=out_tok, cost=cost)
    return values


def _record_usage(*, config, model=None, in_tok=0, out_tok=0, cost=ZERO,
                  status=AIUsageRecord.Status.OK, error="",
                  label=None, model_id=None, was_free=None):
    """Atomically add ``cost`` to the doctor's monthly spend and write an audit row.

    Works for both the LLM draft (pass ``model``) and STT (pass ``model=None`` +
    ``label``/``model_id``/``was_free``)."""
    period = current_period()
    cost = Decimal(cost or 0)
    with transaction.atomic():
        spend, _ = (
            DoctorMonthlySpend.objects
            .select_for_update()
            .get_or_create(clinic_id=config.clinic_id, doctor=config.doctor, period=period)
        )
        spend.spent_usd = (spend.spent_usd or ZERO) + cost
        spend.save(update_fields=["spent_usd", "updated_at"])
        AIUsageRecord.objects.create(
            clinic_id=config.clinic_id,
            doctor=config.doctor,
            model=model,
            model_label=label if label is not None else (model.display_name if model else ""),
            openrouter_model_id=model_id if model_id is not None else (model.openrouter_model_id if model else ""),
            period=period,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            was_free=was_free if was_free is not None else (model.is_free if model else False),
            status=status,
            error=(error or "")[:255],
        )


# ── OpenRouter client ──────────────────────────────────────────────────────────
def _openrouter_chat(*, model_id, system, user, price_hint=(None, None)):
    """Call OpenRouter's chat-completions endpoint. Returns (text, in_tok, out_tok, cost)."""
    api_key = getattr(settings, "OPENROUTER_API_KEY", "")
    if not api_key:
        raise OpenRouterError("The AI service isn't configured. Contact support.")

    base = getattr(settings, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    # Optional attribution headers (recommended by OpenRouter).
    referer = getattr(settings, "OPENROUTER_APP_URL", "")
    title = getattr(settings, "OPENROUTER_APP_TITLE", "")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title

    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": int(getattr(settings, "AI_SCRIBE_MAX_OUTPUT_TOKENS", 1500)),
        "temperature": 0.2,
        # Ask OpenRouter to return the actual generation cost in `usage`.
        "usage": {"include": True},
    }

    try:
        resp = requests.post(
            f"{base}/chat/completions", headers=headers, json=body,
            timeout=int(getattr(settings, "AI_SCRIBE_TIMEOUT_SECONDS", 60)),
        )
    except requests.RequestException as exc:
        logger.error("[AI_SCRIBE] OpenRouter request failed: %r", exc)
        raise OpenRouterError("Couldn't reach the AI service. Please try again.")

    if resp.status_code != 200:
        # Log status + a short snippet only (never the prompt/PHI).
        logger.error("[AI_SCRIBE] OpenRouter HTTP %s: %s", resp.status_code, resp.text[:300])
        raise OpenRouterError("The AI service returned an error. Try again or pick another model.")

    try:
        data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        raise OpenRouterError("The AI service returned an unexpected response.")

    usage = data.get("usage") or {}
    in_tok = int(usage.get("prompt_tokens") or 0)
    out_tok = int(usage.get("completion_tokens") or 0)
    cost = _coerce_cost(usage.get("cost"), in_tok, out_tok, price_hint)
    return text, in_tok, out_tok, cost


def _coerce_cost(reported, in_tok, out_tok, price_hint):
    """Use OpenRouter's reported cost; fall back to price-hint estimation."""
    if reported is not None:
        try:
            return Decimal(str(reported))
        except (InvalidOperation, TypeError):
            pass
    pin, pout = price_hint
    cost = ZERO
    if pin:
        cost += Decimal(in_tok) / Decimal(1_000_000) * Decimal(pin)
    if pout:
        cost += Decimal(out_tok) / Decimal(1_000_000) * Decimal(pout)
    return cost


# ── Speech-to-text (Phase 2) ───────────────────────────────────────────────────
def _stt_provider():
    return (getattr(settings, "STT_PROVIDER", "openrouter") or "openrouter").lower()


def _stt_model():
    return getattr(settings, "STT_MODEL", "openai/whisper-large-v3")


def stt_configured():
    """True if the active STT provider has a usable key."""
    if _stt_provider() == "openai":
        return bool(getattr(settings, "STT_API_KEY", ""))
    return bool(getattr(settings, "OPENROUTER_API_KEY", ""))


def _stt_price_per_minute():
    try:
        return Decimal(str(getattr(settings, "STT_PRICE_PER_MINUTE", "0") or "0"))
    except (InvalidOperation, TypeError):
        return ZERO


def _stt_is_paid():
    """Whether transcription consumes the monthly budget. OpenRouter bills per
    use (so always paid); the openai-compatible path is paid only if a per-minute
    price is configured."""
    if _stt_provider() == "openai":
        return _stt_price_per_minute() > ZERO
    return True


_AUDIO_FORMATS = ("wav", "mp3", "ogg", "flac", "m4a", "aac", "aiff", "webm", "mp4", "pcm16", "pcm24")


def _audio_format(filename, content_type):
    """Best-effort audio container/format string for the request body."""
    name = (filename or "").lower()
    ct = (content_type or "").lower()
    for ext in _AUDIO_FORMATS:
        if name.endswith("." + ext) or ext in ct:
            return "m4a" if ext == "mp4" else ext
    return "webm"  # MediaRecorder's default in most browsers


def transcribe_audio(*, config, file_obj, filename, content_type, language=None):
    """Transcribe an uploaded audio file to text via the configured STT provider.

    Budget-gated (recording costs money even with a free draft model); cost is
    metered into the doctor's monthly spend. Audio is never stored. Returns
    ``(text, cost, duration_seconds)``.
    """
    if config is None or not config.is_enabled:
        raise AIScribeDisabled("AI scribe isn't enabled for you at this clinic.")

    if _stt_is_paid() and remaining_budget(config) <= ZERO:
        raise BudgetExceeded(
            "Your monthly AI budget is used up — voice transcription needs budget. "
            "Type the notes instead, or ask your clinic admin to raise the limit."
        )

    text, duration, cost = _stt_transcribe(file_obj, filename, content_type, language=language)
    if cost is None:  # openai-compatible path doesn't return cost → derive from price
        price = _stt_price_per_minute()
        cost = (Decimal(str(duration)) / Decimal(60) * price) if price > ZERO else ZERO
    cost = Decimal(cost or 0)

    stt_model = _stt_model()
    _record_usage(
        config=config, model=None, cost=cost,
        label=f"STT · {stt_model}", model_id=stt_model, was_free=(cost == ZERO),
    )
    return text, cost, duration


def transcribe_preview(file_obj, filename, content_type, language=None):
    """Admin/diagnostic transcription — no doctor config, budget, or metering.
    Calls the live STT provider and returns ``(text, cost, duration_seconds)``."""
    if not stt_configured():
        raise STTError("Voice transcription isn't configured (no provider key set).")
    text, duration, cost = _stt_transcribe(file_obj, filename, content_type, language=language)
    if cost is None:
        price = _stt_price_per_minute()
        cost = (Decimal(str(duration)) / Decimal(60) * price) if price > ZERO else ZERO
    return text, Decimal(cost or 0), duration


def _stt_transcribe(file_obj, filename, content_type, language=None):
    """Dispatch to the configured STT provider. Returns ``(text, duration, cost)``
    where ``cost`` is a Decimal (OpenRouter) or None (derive from price)."""
    if _stt_provider() == "openai":
        return _stt_openai_compatible(file_obj, filename, content_type, language)
    return _stt_openrouter(file_obj, filename, content_type, language)


def _stt_openrouter(file_obj, filename, content_type, language=None):
    """OpenRouter dedicated transcription endpoint (JSON + base64) — reuses
    OPENROUTER_API_KEY. Returns ``(text, duration, cost)``."""
    import base64

    api_key = getattr(settings, "OPENROUTER_API_KEY", "")
    if not api_key:
        raise STTError("Voice transcription isn't configured. Contact support.")
    base = getattr(settings, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

    audio_bytes = file_obj.read() if hasattr(file_obj, "read") else file_obj
    b64 = base64.b64encode(audio_bytes).decode("ascii")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    referer = getattr(settings, "OPENROUTER_APP_URL", "")
    title = getattr(settings, "OPENROUTER_APP_TITLE", "")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title

    body = {
        "model": _stt_model(),
        "input_audio": {"data": b64, "format": _audio_format(filename, content_type)},
    }
    if language:
        body["language"] = language

    try:
        resp = requests.post(
            f"{base}/audio/transcriptions", headers=headers, json=body,
            timeout=int(getattr(settings, "STT_TIMEOUT_SECONDS", 120)),
        )
    except requests.RequestException as exc:
        logger.error("[AI_SCRIBE] OpenRouter STT request failed: %r", exc)
        raise STTError("Couldn't reach the transcription service. Please try again.")
    if resp.status_code != 200:
        logger.error("[AI_SCRIBE] OpenRouter STT HTTP %s: %s", resp.status_code, resp.text[:300])
        raise STTError("The transcription service returned an error. Please try again.")

    try:
        data = resp.json()
        text = (data.get("text") or "").strip()
    except ValueError:
        raise STTError("The transcription service returned an unexpected response.")
    usage = data.get("usage") or {}
    duration = float(usage.get("seconds") or 0)
    cost = usage.get("cost")
    cost = Decimal(str(cost)) if cost is not None else None
    return text, duration, cost


def _stt_openai_compatible(file_obj, filename, content_type, language=None):
    """OpenAI-compatible multipart /audio/transcriptions (OpenAI, Groq). Returns
    ``(text, duration, None)`` — cost is derived from the per-minute price."""
    api_key = getattr(settings, "STT_API_KEY", "")
    if not api_key:
        raise STTError("Voice transcription isn't configured. Contact support.")
    base = getattr(settings, "STT_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    form = {"model": _stt_model(), "response_format": "verbose_json"}
    if language:
        form["language"] = language
    files = {"file": (filename or "audio.webm", file_obj, content_type or "application/octet-stream")}

    try:
        resp = requests.post(
            f"{base}/audio/transcriptions", headers=headers, data=form, files=files,
            timeout=int(getattr(settings, "STT_TIMEOUT_SECONDS", 120)),
        )
    except requests.RequestException as exc:
        logger.error("[AI_SCRIBE] STT request failed: %r", exc)
        raise STTError("Couldn't reach the transcription service. Please try again.")
    if resp.status_code != 200:
        logger.error("[AI_SCRIBE] STT HTTP %s: %s", resp.status_code, resp.text[:300])
        raise STTError("The transcription service returned an error. Please try again.")

    try:
        data = resp.json()
        text = (data.get("text") or "").strip()
        duration = float(data.get("duration") or 0)
    except (ValueError, TypeError):
        text = (resp.text or "").strip()
        duration = 0.0
    return text, duration, None


# ── Prompt building / parsing ──────────────────────────────────────────────────
def _build_prompts(sections, transcript):
    fields = [{"key": s["name"], "label": s["label"], "type": s["type"]} for s in sections]
    system = (
        "You are a medical scribe assistant. Convert the doctor's raw visit notes or "
        "dictation into a structured clinical note. Respond with STRICT JSON only: a "
        "single object whose keys are exactly the provided field keys and whose values "
        "are plain-text strings for that section. Use the SAME language as the input "
        "(Arabic or English). Include only information actually present in the input; if "
        "a section was not discussed, use an empty string. NEVER invent diagnoses, "
        "medications, doses, or measurements that are not in the input. No text outside the JSON."
    )
    user = (
        "Fields to fill (JSON keys):\n"
        + json.dumps(fields, ensure_ascii=False)
        + '\n\nVisit notes / dictation:\n"""\n'
        + transcript.strip()
        + '\n"""\n\nReturn only the JSON object.'
    )
    return system, user


def _parse_sections(text, sections):
    """Tolerantly extract the JSON object and keep only known, non-empty string keys."""
    valid_keys = {s["name"] for s in sections}
    raw = (text or "").strip()

    if raw.startswith("```"):
        raw = raw.strip("`")
        nl = raw.find("\n")
        if nl != -1:
            raw = raw[nl + 1:]

    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}

    out = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if key in valid_keys and isinstance(value, str) and value.strip():
                out[key] = value.strip()
    return out
