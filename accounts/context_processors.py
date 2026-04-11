def language_context(request):
    """
    Injects language/direction helpers into every template context.

    CURRENT_LANG  – 'ar' or 'en'
    IS_RTL        – True when Arabic
    DIR           – 'rtl' or 'ltr'  (handy for inline dir= attributes)
    """
    lang = getattr(request, "LANGUAGE_CODE", "ar")
    is_rtl = lang == "ar"
    return {
        "CURRENT_LANG": lang,
        "IS_RTL": is_rtl,
        "DIR": "rtl" if is_rtl else "ltr",
    }
