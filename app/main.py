import hmac
from fastapi import FastAPI, Form, Query, HTTPException, BackgroundTasks, Request, Header, Depends
from fastapi.responses import PlainTextResponse, Response
from typing import Optional
import os
from datetime import date
from xml.sax.saxutils import escape as xml_escape
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator
from app.database import save_message, get_recent_messages, save_entries, get_recent_entries, upload_report, save_stock_transaction, get_stock_transactions_for_item
from app.extract import extract_entries
from app.intent import classify_intent
from app.report import generate_report_workbook, generate_stock_report_workbook, resolve_period
from app.stock import parse_stock_message, is_report_command

load_dotenv()

app = FastAPI(title="WhatsApp Store Ledger")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
API_ACCESS_KEY = os.getenv("API_ACCESS_KEY")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

twilio_client = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
twilio_validator = RequestValidator(os.getenv("TWILIO_AUTH_TOKEN"))


def require_api_key(x_api_key: Optional[str] = Header(None)):
    """Shared-secret gate for read endpoints exposing ledger data (PII, pricing, vendor info)."""
    if not API_ACCESS_KEY or not x_api_key or not hmac.compare_digest(x_api_key, API_ACCESS_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


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


async def apply_stock_command(message_id: str, cmd: dict) -> str:
    """Saves one stock movement and returns its confirmation line, including the
    running balance for that item computed from the full transaction history."""
    await save_stock_transaction(message_id, cmd["direction"], cmd["item"], cmd["quantity"], cmd["unit"])
    history = await get_stock_transactions_for_item(cmd["item"])
    balance = sum(
        float(r["quantity"]) if r["direction"] == "in" else -float(r["quantity"])
        for r in history
    )
    unit = cmd["unit"] or ""
    if cmd["direction"] == "in":
        return f"✅ Stock IN: {cmd['item']} +{cmd['quantity']:g}{unit} (balance: {balance:g}{unit})"
    return f"📤 Stock OUT: {cmd['item']} -{cmd['quantity']:g}{unit} (balance: {balance:g}{unit})"


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


async def send_stock_report_async(to_whatsapp: str, period: str | None):
    """Same async-delivery pattern as send_report_async, for the stock IN/OUT report."""
    try:
        start, end = resolve_period(period)
        print(f"📦 Generating stock report for {start} to {end}...")
        content = await generate_stock_report_workbook(start, end)
        filename = f"stock_{start.strftime('%Y-%m')}.xlsx"
        signed_url = await upload_report(filename, content)
        twilio_client.messages.create(
            from_=os.getenv("TWILIO_WHATSAPP_FROM"),
            to=to_whatsapp,
            body=f"Here's your stock report for {start.strftime('%B %Y')}",
            media_url=[signed_url],
        )
        print(f"✅ Sent stock report to {to_whatsapp}")
    except Exception as e:
        print(f"⚠️  Failed to generate/send stock report: {e}")


# ── HEALTH CHECK ──
@app.get("/")
async def root():
    return {"status": "WhatsApp Ledger backend is running"}


# ── READ RAW MESSAGES ──
@app.get("/messages", dependencies=[Depends(require_api_key)])
async def list_messages(limit: int = 20):
    messages = await get_recent_messages(limit)
    return {"count": len(messages), "messages": messages}


# ── READ STRUCTURED ENTRIES (Phase 2) ──
@app.get("/entries", dependencies=[Depends(require_api_key)])
async def list_entries(limit: int = 50):
    entries = await get_recent_entries(limit)
    return {"count": len(entries), "entries": entries}


# ── EXCEL EXPORT (Phase 3) ──
@app.get("/export", dependencies=[Depends(require_api_key)])
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
@limiter.limit("20/minute")
async def receive_twilio_message(
    request: Request,
    background_tasks: BackgroundTasks,
    x_twilio_signature: Optional[str] = Header(None, alias="X-Twilio-Signature"),
    From: str = Form(...),
    To: str = Form(...),
    Body: Optional[str] = Form(None),
    NumMedia: Optional[str] = Form("0"),
    MediaUrl0: Optional[str] = Form(None),
    MediaContentType0: Optional[str] = Form(None),
    MessageSid: Optional[str] = Form(None),
    ProfileName: Optional[str] = Form(None),
):
    # Reject anything that isn't actually from Twilio — otherwise anyone who
    # finds this URL can inject fake ledger entries or trigger the app's
    # Twilio account to message arbitrary numbers.
    full_form = await request.form()
    if not twilio_validator.validate(str(request.url), dict(full_form), x_twilio_signature or ""):
        print(f"⚠️  Rejected webhook: invalid Twilio signature (validated against url={request.url})")
        raise HTTPException(status_code=403, detail="Invalid signature")

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

        # Step 2: Stock commands (IN/OUT/REPORT) — checked before the general LLM
        # intent classifier, so it never collides with ordinary chat. A single Groq
        # call extracts every movement from the (possibly multi-line) message; each
        # extracted quantity is then cross-checked against the raw text before being
        # trusted, so a hallucinated number gets dropped rather than saved.
        if msg_type == "text" and text.strip() and saved_message:
            stock_msg = await parse_stock_message(text)

            if stock_msg is not None:
                reply_lines = [await apply_stock_command(saved_message["id"], cmd) for cmd in stock_msg["commands"]]
                if not reply_lines:
                    reply = "Couldn't read that stock command. Try: IN Cement 50 bags"
                else:
                    reply = "\n".join(reply_lines)
                    if len(stock_msg["commands"]) < stock_msg["attempted"]:
                        reply += "\n⚠️ Some lines couldn't be confidently read — please check and resend those."
                twiml = f"<Response><Message><Body>{xml_escape(reply)}</Body></Message></Response>"
                return Response(content=twiml, media_type="application/xml")

            elif is_report_command(text):
                period = "this_month"
                trailing = text.strip()[len("REPORT"):].strip().lower()
                if "last month" in trailing:
                    period = "last_month"
                background_tasks.add_task(send_stock_report_async, From, period)
                twiml = (
                    "<Response><Message>"
                    "<Body>📦 Got it — generating your stock report now, I'll send it in a moment.</Body>"
                    "</Message></Response>"
                )
                return Response(content=twiml, media_type="application/xml", background=background_tasks)

        # Step 3: Classify intent, then route (Phase 3)
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

            elif intent == "stock_report_request":
                background_tasks.add_task(send_stock_report_async, From, intent_result.get("period"))
                twiml = (
                    "<Response><Message>"
                    "<Body>📦 Got it — generating your stock report now, I'll send it in a moment.</Body>"
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
