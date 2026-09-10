"""
Password-gated audit log request — lets the owner ask for the raw message
log (who sent what, when) over WhatsApp, e.g. "send me this month logs
password pineapple". Deterministic, no LLM involved: a security-sensitive
trigger shouldn't depend on model classification.

Deliberately separate from /export's X-API-Key gate — this is the WhatsApp-
reachable path, so it needs its own credential. The password is compared
with hmac.compare_digest to avoid timing attacks, same as the API key check.
"""

import os
import re
import hmac

LOG_ATTEMPT_RE = re.compile(r"\blog(s)?\b", re.IGNORECASE)
PASSWORD_RE = re.compile(r"password\s*[:\-]?\s*\"?(\S+?)\"?[\s.!?]*$", re.IGNORECASE)


def parse_log_request(text: str) -> dict | None:
    """Returns None if the message doesn't even mention 'log'/'logs' (falls
    through to normal handling). Otherwise {"authorized": bool, "period": ...} —
    authorized is False if the password is missing or wrong."""
    if not text or not LOG_ATTEMPT_RE.search(text):
        return None

    match = PASSWORD_RE.search(text)
    given_password = match.group(1) if match else ""
    expected_password = os.getenv("AUDIT_LOG_PASSWORD") or ""
    authorized = bool(expected_password) and hmac.compare_digest(given_password, expected_password)

    period = "last_month" if "last month" in text.lower() else "this_month"
    return {"authorized": authorized, "period": period}
