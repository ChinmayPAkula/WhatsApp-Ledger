"""
Intent classification — decides what an inbound WhatsApp text message is for,
before it's routed to ledger extraction or report generation.
"""

import json
from groq import Groq
from app.extract import groq_client as _shared_client

groq_client: Groq = _shared_client

SYSTEM_PROMPT = """You classify a single WhatsApp message sent to a fast food restaurant owner's ledger bot.
This bot tracks two separate things: a spend/vendor LEDGER (orders and deliveries with prices) and a
separate physical STOCK log (items received/consumed, no prices, tracked via IN/OUT commands).

Return ONLY a JSON object of this exact shape:
{
  "intent": "entry" | "report_request" | "stock_report_request" | "other",
  "period": "this_month" | "last_month" | "YYYY-MM" | null
}

INTENT DEFINITIONS:
- "entry": the message is about an order placed with a vendor or a delivery that arrived — items, quantities, prices. Examples: "tomato 5kg, onion 10kg", "received paneer 2kg @350", "need 5kg oil tomorrow".
- "report_request": the sender wants the spend/vendor ledger report (prices, spend by category, vendor totals). Examples: "send me the excel sheet", "can I get this month's report", "send July's sheet", "give me the statistics", "export the ledger".
- "stock_report_request": the sender wants the STOCK/INVENTORY report specifically — current stock levels, or the IN/OUT transaction history. Look for words like "stock", "inventory", "in out report", "in/out sheet", "stock levels". Examples: "send the stock report", "give me the in out report", "what's my current stock", "send inventory sheet".
- "other": anything else — greetings, thanks, unrelated chat. Examples: "ok thanks", "good morning", "are you there?".

If the message doesn't clearly say "stock"/"inventory"/"in out"/"in/out", assume it means the regular
ledger report_request, not stock_report_request — don't guess stock intent from ambiguous wording.

PERIOD (only meaningful when intent is "report_request" or "stock_report_request", else null):
- "this_month" if they mean the current month or don't specify.
- "last_month" if they say "last month" / "previous month".
- "YYYY-MM" if they name a specific month (e.g. "July" in a message sent in 2026 → "2026-07"; assume the most recent occurrence of that month that isn't in the future).

Return ONLY the JSON object. No markdown, no code fences, no explanation.
"""


async def classify_intent(message_text: str, message_timestamp: str = None) -> dict:
    """
    Classify an inbound text message. Returns {"intent": ..., "period": ...}.
    Defaults to {"intent": "other", "period": None} if classification fails,
    so a failure never accidentally triggers report generation or blocks logging.
    """
    fallback = {"intent": "other", "period": None}
    if not message_text or not message_text.strip():
        return fallback

    try:
        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message_text},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=500,
        )
        parsed = json.loads(completion.choices[0].message.content)
        intent = parsed.get("intent")
        if intent not in ("entry", "report_request", "stock_report_request", "other"):
            return fallback
        return {"intent": intent, "period": parsed.get("period")}
    except Exception as e:
        print(f"⚠️  Intent classification failed: {e}")
        return fallback
