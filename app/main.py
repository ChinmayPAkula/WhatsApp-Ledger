from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse
import httpx
import os
from dotenv import load_dotenv
from app.database import save_message, get_recent_messages
from app.models import WhatsAppPayload

load_dotenv()

app = FastAPI(title="WhatsApp Store Ledger")

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")


# ── WEBHOOK VERIFICATION (Meta calls this once when you register the URL) ──
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        print("✅ Webhook verified by Meta")
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── MESSAGE RECEIVER (Meta calls this every time a worker sends a message) ──
@app.post("/webhook")
async def receive_message(request: Request):
    body = await request.json()

    try:
        payload = WhatsAppPayload(**body)
        for entry in payload.entry:
            for change in entry.changes:
                messages = change.value.messages
                if not messages:
                    continue

                for msg in messages:
                    sender   = msg.get("from")          # worker phone number
                    msg_type = msg.get("type")           # text / image / etc
                    timestamp = msg.get("timestamp")

                    text = None
                    media_id = None

                    if msg_type == "text":
                        text = msg.get("text", {}).get("body")
                    elif msg_type == "image":
                        media_id = msg.get("image", {}).get("id")
                        text = msg.get("image", {}).get("caption", "")

                    print(f"📩 From: {sender} | Type: {msg_type} | Text: {text}")

                    # Save to Supabase
                    await save_message(
                        sender=sender,
                        msg_type=msg_type,
                        text=text,
                        media_id=media_id,
                        timestamp=timestamp,
                        raw=body,
                    )

    except Exception as e:
        print(f"⚠️  Error processing message: {e}")

    # Always return 200 to Meta — otherwise it retries endlessly
    return {"status": "ok"}


# ── HEALTH CHECK ──
@app.get("/")
async def root():
    return {"status": "WhatsApp Ledger backend is running"}


# ── READ RECENT MESSAGES (for quick verification) ──
@app.get("/messages")
async def list_messages(limit: int = 20):
    messages = await get_recent_messages(limit)
    return {"count": len(messages), "messages": messages}
