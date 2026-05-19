from fastapi import FastAPI, Form, Query, HTTPException
from fastapi.responses import PlainTextResponse, Response
from typing import Optional
import os
from dotenv import load_dotenv
from app.database import save_message, get_recent_messages, save_entries, get_recent_entries
from app.extract import extract_entries

load_dotenv()

app = FastAPI(title="WhatsApp Store Ledger")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")


# ── HEALTH CHECK ──
@app.get("/")
async def root():
    return {"status": "WhatsApp Ledger backend is running"}


# ── READ RAW MESSAGES ──
@app.get("/messages")
async def list_messages(limit: int = 20):
    messages = await get_recent_messages(limit)
    return {"count": len(messages), "messages": messages}


# ── READ STRUCTURED ENTRIES (Phase 2) ──
@app.get("/entries")
async def list_entries(limit: int = 50):
    entries = await get_recent_entries(limit)
    return {"count": len(entries), "entries": entries}


# ── TWILIO WEBHOOK (receives WhatsApp messages) ──
@app.post("/webhook")
async def receive_twilio_message(
    From: str = Form(...),
    To: str = Form(...),
    Body: Optional[str] = Form(None),
    NumMedia: Optional[str] = Form("0"),
    MediaUrl0: Optional[str] = Form(None),
    MediaContentType0: Optional[str] = Form(None),
    MessageSid: Optional[str] = Form(None),
    ProfileName: Optional[str] = Form(None),
):
    try:
        sender_phone = From.replace("whatsapp:", "").lstrip("+")
        num_media = int(NumMedia or "0")
        msg_type = "image" if num_media > 0 else "text"
        media_id = MediaUrl0 if num_media > 0 else None
        text = Body or ""

        print(f"📩 From: {sender_phone} ({ProfileName}) | Type: {msg_type} | Text: {text}")

        # Step 1: Save raw message (always — Phase 1 behavior)
        saved_message = await save_message(
            sender=sender_phone,
            msg_type=msg_type,
            text=text,
            media_id=media_id,
            timestamp=None,
            raw={
                "From": From, "To": To, "Body": Body, "NumMedia": NumMedia,
                "MediaUrl0": MediaUrl0, "MediaContentType0": MediaContentType0,
                "MessageSid": MessageSid, "ProfileName": ProfileName,
            },
        )

        # Step 2: Extract structured entries via Groq (Phase 2)
        if msg_type == "text" and text.strip() and saved_message:
            print(f"🤖 Extracting structured entries...")
            entries = await extract_entries(text)
            if entries:
                await save_entries(saved_message["id"], entries)
                for e in entries:
                    print(f"   ✓ {e.get('entry_type')} | {e.get('item')} | "
                          f"qty={e.get('quantity')} {e.get('unit') or ''} | "
                          f"₹{e.get('price_per_unit') or '-'}/u | "
                          f"cat={e.get('category')}")
            else:
                print(f"   (no entries extracted)")

    except Exception as e:
        print(f"⚠️  Error processing message: {e}")

    return Response(content="<Response></Response>", media_type="application/xml")


# ── META VERIFICATION (legacy, unused) ──
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")
