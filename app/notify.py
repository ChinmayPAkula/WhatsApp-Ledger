"""
Shared WhatsApp notification helpers — the list of numbers that receive
proactive messages (monthly reports, security alerts), and the security
alert itself.
"""

import os
from twilio.rest import Client


def get_recipients() -> list[str]:
    """OWNER_WHATSAPP_TO is a comma-separated list, e.g.
    'whatsapp:+91...,whatsapp:+91...' — one owner or several."""
    raw = os.getenv("OWNER_WHATSAPP_TO", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


async def send_security_alert_async(sender_phone: str, attempted_message: str):
    """Broadcasts a wrong-password audit-log attempt to every configured
    recipient. The attempt itself is already in the messages table via the
    normal save_message() call every inbound message gets — this is the
    proactive, hard-to-miss notification on top of that passive record."""
    try:
        client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        from_number = os.getenv("TWILIO_WHATSAPP_FROM")
        body = (
            f"🚨 Security alert: incorrect password on an activity-log request "
            f"from +{sender_phone}.\nMessage: \"{attempted_message}\""
        )
        for recipient in get_recipients():
            client.messages.create(from_=from_number, to=recipient, body=body)
        print(f"🚨 Sent security alert for failed log-password attempt from {sender_phone}")
    except Exception as e:
        print(f"⚠️  Failed to send security alert: {e}")
