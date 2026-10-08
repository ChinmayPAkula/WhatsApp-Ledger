"""
Order command parsing — "Order:" followed by item lines, sent when staff place
an order with a vendor. Same fixed-vocabulary, deterministic-gate approach as
stock.py: the gate is plain regex, one Groq call extracts the lines, and every
quantity is cross-checked against the raw text before it's trusted.

Kept separate from the free-text ledger entries (entries table): an explicit
"Order:" message goes only to the orders table, so nothing is logged twice.
"""

import re
import json
from datetime import datetime
from groq import Groq
from app.extract import groq_client as _shared_client
from app.canonicalize import get_known_items, canonicalize
from app.units import normalize_quantity
from app.stock import STOCK_HEADER_RE, OTHER_HEADER_RE, _quantity_verified_in_text
from app.report import IST, _sender_label

groq_client: Groq = _shared_client

# The colon is required for the inline form ("Order: Milk 2 ltr") so an ordinary
# sentence like "order of 5 boxes arrived" never matches; a bare "Order" is only
# accepted alone on its own line, as a header.
ORDER_HEADER_RE = re.compile(r"^[ \t]*ORDER[ \t]*:?[ \t]*$", re.IGNORECASE)
ORDER_INLINE_RE = re.compile(r"^[ \t]*ORDER[ \t]*:[ \t]*\S.*\d", re.IGNORECASE)

EXTRACT_SYSTEM_PROMPT = """You extract vendor order lines from a WhatsApp message sent to an
ordering bot. The message starts with "Order:" (any capitalization) and lists the items being
ordered, one per line, e.g.:

Order:
Milk 2 ltr
Onion 5 kg

or on one line: "Order: Milk 2 ltr".

For each item line, extract:
- item: the item name, spelling-corrected and lowercase
- quantity: the number, EXACTLY as written in the message — never invent, round, or convert a
  spelled-out word into a digit; if no literal numeral is present for a line, omit that line
- unit: the unit if mentioned (e.g. "kg", "ltr", "pcs"), else null

Return ONLY a JSON object of this exact shape:
{"items": [{"item": "<name>", "quantity": <number>, "unit": "<unit>"|null}, ...]}

Ignore the "Order:" header itself and any line that isn't clearly an item. Return ONLY the JSON
object, no markdown, no explanation.
"""


def count_order_attempts(text: str) -> int:
    """How many lines look like an attempted order item: every inline
    "Order: ..." line, plus every item line (one containing a digit) under an
    Order: header. The block ends at the next header of any kind."""
    attempted = 0
    under_header = False
    for line in text.splitlines():
        if ORDER_HEADER_RE.match(line):
            under_header = True
        elif ORDER_INLINE_RE.match(line):
            attempted += 1
            under_header = False
        elif STOCK_HEADER_RE.match(line) or OTHER_HEADER_RE.match(line):
            under_header = False
        elif under_header and re.search(r"\d", line):
            attempted += 1
    return attempted


async def extract_order_items(text: str) -> list[dict]:
    """One Groq call over the whole message. Returns only items whose quantity
    is independently verified to appear literally in the source text."""
    try:
        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=4000,
            extra_body={"reasoning_effort": "low"},
        )
        items = json.loads(completion.choices[0].message.content).get("items", [])
        if not isinstance(items, list):
            return []
    except Exception as e:
        print(f"⚠️  Order extraction failed: {e}")
        return []

    verified = []
    for m in items:
        item, quantity = m.get("item"), m.get("quantity")
        if not item or quantity is None:
            continue
        if not _quantity_verified_in_text(quantity, text):
            print(f"⚠️  Rejected order item (quantity not found verbatim in message): {m}")
            continue
        qty, unit, _ = normalize_quantity(float(quantity), m.get("unit"))
        verified.append({"item": str(item).strip().lower(), "quantity": qty, "unit": unit})

    if verified:
        known_items = await get_known_items("orders")
        for entry in verified:
            canonical = canonicalize(entry["item"], known_items)
            if canonical != entry["item"]:
                print(f"   📎 Canonicalized order item: {entry['item']!r} -> {canonical!r}")
            entry["item"] = canonical

    return verified


async def parse_order_message(text: str) -> dict | None:
    """Returns None if the message isn't an order attempt (falls through to
    normal handling). Otherwise {"items": [...verified items...], "attempted":
    <count of lines that looked like an attempt>}."""
    if not text:
        return None
    attempted = count_order_attempts(text)
    if not attempted:
        return None
    return {"items": await extract_order_items(text), "attempted": attempted}


def format_last_ordered(item: dict, last: dict | None, now: datetime | None = None) -> str:
    """One reply line per ordered item, including when it was last ordered and
    by whom — so the owner can spot odd ordering frequency at a glance."""
    qty = f"{item['quantity']:g}{item.get('unit') or ''}"
    if not last:
        return f"{item['item'].title()} {qty} — first time ordered"
    when = datetime.fromisoformat(last["created_at"].replace("Z", "+00:00")).astimezone(IST)
    days = ((now or datetime.now(IST)).astimezone(IST).date() - when.date()).days
    ago = "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"
    by = _sender_label(last)
    return f"{item['item'].title()} {qty} — last ordered {when.strftime('%d %b')} ({ago})" + (f" by {by}" if by else "")
