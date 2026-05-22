def time_format(request):
    """Expose the current user's 24h/12h time preference to every template.

    ``CLOCK_12H`` is the boolean the ``clock`` filter and the JS clocks read.
    Anonymous/non-secretary users default to 24-hour.
    """
    pref = getattr(getattr(request, "user", None), "time_format", "24")
    return {"CLOCK_12H": pref == "12"}
