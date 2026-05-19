import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ── MESSAGES (raw, from Phase 1) ──

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
    print(f"💾 Saved message: {result.data[0]['id'] if result.data else 'none'}")
    return result.data[0] if result.data else None


async def get_recent_messages(limit: int = 20) -> list[dict]:
    result = (
        supabase.table("messages")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


# ── ENTRIES (structured, from Phase 2) ──

async def save_entries(message_id: str, entries: list[dict]) -> list[dict]:
    """Save a list of structured entries linked to a raw message."""
    if not entries:
        return []

    rows = []
    for entry in entries:
        rows.append({
            "message_id": message_id,
            "entry_type": entry.get("entry_type"),
            "item": entry.get("item"),
            "quantity": entry.get("quantity"),
            "unit": entry.get("unit"),
            "category": entry.get("category"),
            "price_per_unit": entry.get("price_per_unit"),
            "total_price": entry.get("total_price"),
            "notes": entry.get("notes"),
            "status": entry.get("status", "confirmed"),
        })

    result = supabase.table("entries").insert(rows).execute()
    print(f"💾 Saved {len(result.data)} entries to Supabase")
    return result.data


async def get_recent_entries(limit: int = 50) -> list[dict]:
    result = (
        supabase.table("entries")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data
