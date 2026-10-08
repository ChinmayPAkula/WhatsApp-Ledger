"""
Order command parsing — "Order:" followed by item lines, sent when staff place
an order with a vendor. Built on the shared line-command machinery
(line_commands.py): a deterministic regex gate, one Groq call, and a verbatim
quantity cross-check before anything is trusted.

Kept separate from the free-text ledger entries (entries table): an explicit
"Order:" message goes only to the orders table, so nothing is logged twice.
"""

from datetime import datetime
from app.line_commands import build_gate, count_attempts, extract_lines, parse_lines
from app.report import IST, _sender_label

ORDER_HEADER_RE, ORDER_INLINE_RE = build_gate(r"ORDER")

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
    return count_attempts(text, ORDER_HEADER_RE, ORDER_INLINE_RE)


async def extract_order_items(text: str) -> list[dict]:
    return await extract_lines(text, EXTRACT_SYSTEM_PROMPT, "orders", "order")


async def parse_order_message(text: str) -> dict | None:
    """None if the message isn't an order attempt (falls through to normal
    handling), else {"items": [...verified...], "attempted": <line count>}."""
    return await parse_lines(text, ORDER_HEADER_RE, ORDER_INLINE_RE, EXTRACT_SYSTEM_PROMPT, "orders", "order")


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
