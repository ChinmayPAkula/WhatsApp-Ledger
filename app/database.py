import os
from datetime import date, datetime
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
            "vendor": entry.get("vendor"),
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


async def get_entries_between(start: date, end: date) -> list[dict]:
    """Entries created in [start, end) — end is exclusive, both as calendar dates.
    Joins messages to bring in sender_phone."""
    result = (
        supabase.table("entries")
        .select("*, messages(sender_phone)")
        .gte("created_at", start.isoformat())
        .lt("created_at", end.isoformat())
        .order("created_at", desc=False)
        .execute()
    )
    rows = result.data
    for row in rows:
        message = row.pop("messages", None) or {}
        row["sender_phone"] = message.get("sender_phone")
    return rows


REPORTS_BUCKET = "reports"


async def upload_report(filename: str, content: bytes) -> str:
    """Upload a report file to Supabase Storage and return a signed URL (valid 1 hour)."""
    supabase.storage.from_(REPORTS_BUCKET).upload(
        filename,
        content,
        {"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "upsert": "true"},
    )
    signed = supabase.storage.from_(REPORTS_BUCKET).create_signed_url(filename, 3600)
    return signed["signedURL"] if "signedURL" in signed else signed["signed_url"]


async def get_entries_before(before: date) -> list[dict]:
    """Full (item, vendor) price history before a period, oldest first, for price-change lookups."""
    result = (
        supabase.table("entries")
        .select("item, vendor, price_per_unit, created_at")
        .lt("created_at", before.isoformat())
        .not_.is_("price_per_unit", "null")
        .order("created_at", desc=False)
        .execute()
    )
    return result.data
