from django.utils import translation


ROLE_LANGUAGE_DEFAULTS = {
    "DOCTOR": "en",
    "MAIN_DOCTOR": "ar",
    "SECRETARY": "ar",
    "PATIENT": "ar",
}


def get_default_language_for_role(role):
    """Return the default language code for a given user role."""
    return ROLE_LANGUAGE_DEFAULTS.get(role, "ar")


class LanguagePreferenceMiddleware:
    """
    Determines the active language for each request using this priority order:
      1. User's saved DB preference (preferred_language)
      2. Cookie 'lang'
      3. Role-based default (DOCTOR → en, all others → ar)

    When a new authenticated user has no preference yet, the role default is
    saved to the DB + cookie so subsequent requests resolve instantly.

    Runs AFTER AuthenticationMiddleware so request.user is available,
    and AFTER LocaleMiddleware so we can override its detection.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        lang = self._resolve_language(request)
        translation.activate(lang)
        request.LANGUAGE_CODE = lang

        response = self.get_response(request)

        # Keep cookie in sync so LocaleMiddleware picks it up on the next hit
        # and SSR sends the correct dir on the very first load.
        response.set_cookie(
            "lang",
            lang,
            max_age=365 * 24 * 3600,
            samesite="Lax",
        )
        return response

    def _resolve_language(self, request):
        user = request.user

        # 1. DB preference (authenticated users only)
        if user.is_authenticated:
            pref = getattr(user, "preferred_language", None)
            if pref in ("ar", "en"):
                return pref

            # No preference yet — compute role default, persist it
            default = get_default_language_for_role(getattr(user, "role", "PATIENT"))
            try:
                user.preferred_language = default
                user.save(update_fields=["preferred_language"])
            except Exception:
                pass
            return default

        # 2. Cookie (anonymous visitors)
        cookie_lang = request.COOKIES.get("lang")
        if cookie_lang in ("ar", "en"):
            return cookie_lang

        # 3. Hard default
        return "ar"
