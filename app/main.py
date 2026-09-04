from fastapi import FastAPI, Form, Query, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse, Response
from typing import Optional
import os
from datetime import date
from xml.sax.saxutils import escape as xml_escape
from dotenv import load_dotenv
from twilio.rest import Client as TwilioClient
from app.database import save_message, get_recent_messages, save_entries, get_recent_entries, upload_report
from app.extract import extract_entries
from app.intent import classify_intent
from app.report import generate_report_workbook, resolve_period

load_dotenv()

app = FastAPI(title="WhatsApp Store Ledger")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")

twilio_client = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))


def format_entry_confirmation(entries: list[dict]) -> str:
    """Build a human-readable WhatsApp confirmation for parsed entries."""
    lines = []
    for e in entries:
        if e.get("status") == "unclear" or not e.get("item"):
            lines.append(f"❓ Couldn't fully parse: {e.get('notes') or 'this item'}")
            continue
        qty = f"{e['quantity']}{e.get('unit') or ''}" if e.get("quantity") is not None else ""
        price = f" @ ₹{e['price_per_unit']}/{e.get('unit') or 'unit'}" if e.get("price_per_unit") is not None else ""
        total = f" (₹{e['total_price']} total)" if e.get("total_price") is not None else ""
        vendor = f" — {e['vendor']}" if e.get("vendor") else ""
        kind = "🛒 Order" if e.get("entry_type") == "order" else "📦 Delivery"
        lines.append(f"{kind}: {e['item']} {qty}{price}{total}{vendor}".strip())
    return "✅ Logged:\n" + "\n".join(lines)


async def send_report_async(to_whatsapp: str, period: str | None):
    """Runs after the webhook has already responded to Twilio — generates the
    report and sends it as a separate outbound message, since report generation
    is too slow to fit inside Twilio's synchronous webhook timeout."""
    try:
        start, end = resolve_period(period)
        print(f"📊 Generating report for {start} to {end}...")
        content = await generate_report_workbook(start, end)
        filename = f"ledger_{start.strftime('%Y-%m')}.xlsx"
        signed_url = await upload_report(filename, content)
        twilio_client.messages.create(
            from_=os.getenv("TWILIO_WHATSAPP_FROM"),
            to=to_whatsapp,
            body=f"Here's your report for {start.strftime('%B %Y')}",
            media_url=[signed_url],
        )
        print(f"✅ Sent report to {to_whatsapp}")
    except Exception as e:
        print(f"⚠️  Failed to generate/send report: {e}")


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


# ── EXCEL EXPORT (Phase 3) ──
@app.get("/export")
async def export_report(month: Optional[str] = None):
    """month: 'YYYY-MM', defaults to the current month-to-date."""
    start, end = resolve_period(month)
    content = await generate_report_workbook(start, end)
    filename = f"ledger_{start.strftime('%Y-%m')}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── TWILIO WEBHOOK (receives WhatsApp messages) ──
@app.post("/webhook")
async def receive_twilio_message(
    background_tasks: BackgroundTasks,
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

        # Step 2: Classify intent, then route (Phase 3)
        if msg_type == "text" and text.strip() and saved_message:
            intent_result = await classify_intent(text)
            intent = intent_result["intent"]
            print(f"🧭 Intent: {intent} (period={intent_result.get('period')})")

            if intent == "entry":
                print(f"🤖 Extracting structured entries...")
                entries = await extract_entries(text)
                if entries:
                    await save_entries(saved_message["id"], entries)
                    for e in entries:
                        print(f"   ✓ {e.get('entry_type')} | {e.get('item')} | "
                              f"qty={e.get('quantity')} {e.get('unit') or ''} | "
                              f"₹{e.get('price_per_unit') or '-'}/u | "
                              f"vendor={e.get('vendor') or '-'} | "
                              f"cat={e.get('category')}")
                    confirmation = format_entry_confirmation(entries)
                else:
                    print(f"   (no entries extracted)")
                    confirmation = "🤔 Got your message but couldn't identify any items to log."

                twiml = f"<Response><Message><Body>{xml_escape(confirmation)}</Body></Message></Response>"
                return Response(content=twiml, media_type="application/xml")

            elif intent == "report_request":
                background_tasks.add_task(send_report_async, From, intent_result.get("period"))
                twiml = (
                    "<Response><Message>"
                    "<Body>📊 Got it — generating your report now, I'll send it in a moment.</Body>"
                    "</Message></Response>"
                )
                return Response(content=twiml, media_type="application/xml", background=background_tasks)

            else:
                print("   (not a ledger entry or report request — skipping extraction)")

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
