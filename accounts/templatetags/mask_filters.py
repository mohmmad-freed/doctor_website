from django import template

register = template.Library()


@register.filter
def mask_email(email):
    """
    Masks an email address for display: show first + last char of local part.
    Example: john.doe@gmail.com → j******e@gmail.com
    Returns empty string for None/empty input.
    """
    if not email:
        return ""
    try:
        local, domain = str(email).split("@", 1)
    except ValueError:
        return str(email)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


@register.filter
def mask_phone(phone):
    """
    Masks a phone number for display: show first 2 digits + last 3 digits.
    Example: 0591234567 → 05*****567
    Returns empty string for None/empty input.
    """
    if not phone:
        return ""
    phone = str(phone)
    if len(phone) <= 5:
        return phone
    return phone[:2] + "*" * (len(phone) - 5) + phone[-3:]
