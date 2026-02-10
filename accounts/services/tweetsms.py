import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Known TweetsMS error codes
TWEETSMS_ERRORS = {
    "-100": "Missing parameters",
    "-110": "Wrong credentials (invalid API key)",
    "-113": "Insufficient balance",
    "-115": "Sender not available",
    "-116": "Invalid sender name",
}


def send_sms(to: str, message: str) -> bool:
    """
    Send an SMS via the TweetsMS Legacy HTTP API (GET).

    Args:
        to: Recipient phone number.
        message: SMS body text.

    Returns:
        True if the SMS was accepted by the gateway.

    Raises:
        ValueError: If required settings are missing.
        RuntimeError: If the gateway returns a known error code.
        requests.RequestException: On network / HTTP failures.
    """
    api_key = getattr(settings, "TWEETSMS_API_KEY", "")
    sender = getattr(settings, "TWEETSMS_SENDER", "")
    base_url = getattr(settings, "TWEETSMS_BASE_URL", "https://tweetsms.ps/api.php")

    if not api_key or not sender:
        raise ValueError(
            "TweetsMS is not configured. Set TWEETSMS_API_KEY and TWEETSMS_SENDER."
        )

    params = {
        "comm": "sendsms",
        "api_key": api_key,
        "to": to,
        "message": message,
        "sender": sender,
    }

    logger.info("[TWEETSMS] Sending SMS to=%s sender=%s", to, sender)

    response = requests.get(base_url, params=params, timeout=10)
    body = response.text.strip()

    # TweetsMS response format: "status:message_id:phone:tracking_id<br />"
    # Strip HTML tags and whitespace
    body = re.sub(r"<[^>]+>", "", body).strip()

    # Extract status code (first field before ':')
    status_str = body.split(":")[0].strip()

    logger.info(
        "[TWEETSMS] Response body=%r status=%s status_code=%s",
        body,
        status_str,
        response.status_code,
    )

    try:
        status_code = int(status_str)
    except ValueError:
        logger.error("[TWEETSMS] Non-numeric status sending to=%s: %r", to, body)
        raise RuntimeError(f"TweetsMS unexpected response: {body!r}")

    # Success: status >= 1
    if status_code >= 1:
        logger.info("[TWEETSMS] SMS sent successfully to=%s (response=%s)", to, body)
        return True

    # Known error codes (negative)
    if status_str in TWEETSMS_ERRORS:
        error_msg = TWEETSMS_ERRORS[status_str]
        logger.error(
            "[TWEETSMS] Error sending to=%s: %s (code=%s)", to, error_msg, status_str
        )
        raise RuntimeError(f"TweetsMS error: {error_msg} (code={status_str})")

    # Unknown error
    logger.error("[TWEETSMS] Unknown error sending to=%s: %r", to, body)
    raise RuntimeError(f"TweetsMS unknown error: {body!r}")
