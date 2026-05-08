import os
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


async def save_message(
    sender: str,
    msg_type: str,
    text: str | None,
    media_id: str | None,
    timestamp: str | None,
    raw: dict,
) -> dict:
    """Write an incoming WhatsApp message to the messages table."""
    row = {
        "sender_phone": sender,
        "message_type": msg_type,
        "body": text,
        "media_id": media_id,
        "whatsapp_timestamp": int(timestamp) if timestamp else None,
        "raw_payload": raw,
    }
    result = supabase.table("messages").insert(row).execute()
    print(f"💾 Saved to Supabase: {result.data}")
    return result.data


async def get_recent_messages(limit: int = 20) -> list[dict]:
    """Read the most recent messages from the messages table."""
    result = (
        supabase.table("messages")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data
