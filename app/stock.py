"""
Stock command parsing — IN / OUT / REPORT, a fixed-vocabulary command
language, separate from the free-text ledger entries in extract.py.

Design: a single Groq call extracts every stock movement from the whole
(possibly multi-line, inconsistently-capitalized) message at once — this is
the same "let the LLM handle real-world formatting" approach already proven
in extract_entries(). The hallucination guard isn't in how parsing happens,
it's a cross-check afterward: a movement is only trusted if its exact
quantity literally appears as a number in the source text. An invented or
rounded number gets silently dropped rather than saved.
"""

import re
import json
from groq import Groq
from app.extract import groq_client as _shared_client

groq_client: Groq = _shared_client

# Gate: does this message even look like an attempt at a stock command? Requires
# "in"/"out" (any case) plus a digit somewhere — cheap, no LLM call, and specific
# enough that ordinary sentences ("in the evening we ran out") never match, since
# they don't also carry a bare quantity.
STOCK_ATTEMPT_RE = re.compile(r"^(IN|OUT)\s.*\d", re.IGNORECASE | re.MULTILINE)

EXTRACT_SYSTEM_PROMPT = """You extract stock movement commands from a WhatsApp message sent to
a stock-tracking bot. The message may contain multiple lines, each usually starting with "IN" or
"OUT" (any capitalization), meaning stock received or stock consumed, e.g.:

IN Milk 2litres
IN paneer 4kg
Out mushroom 1kg

For each such line/movement, extract:
- direction: "in" or "out"
- item: the item name, spelling-corrected and lowercase (e.g. "cemnt" -> "cement")
- quantity: the number of units, EXACTLY as written in the message — never invent, round, or
  convert a spelled-out word ("fifty") into a digit; if no literal numeral is present for a
  movement, omit that movement entirely
- unit: the unit if mentioned (e.g. "kg", "litres", "bags"), else null

Return ONLY a JSON object of this exact shape:
{"movements": [{"direction": "in"|"out", "item": "<name>", "quantity": <number>, "unit": "<unit>"|null}, ...]}

Ignore any line that isn't clearly a stock movement. Return ONLY the JSON object, no markdown, no explanation.
"""


def _quantity_verified_in_text(quantity, text: str) -> bool:
    """The actual hallucination guard: rejects any quantity the LLM didn't read
    verbatim off the message. Checks both integer and as-given decimal forms,
    each as a standalone number (not embedded in a larger one, e.g. matching
    "2" inside "20")."""
    try:
        q = float(quantity)
    except (TypeError, ValueError):
        return False
    candidates = {str(q)}
    if q == int(q):
        candidates.add(str(int(q)))
    return any(re.search(rf"(?<!\d){re.escape(c)}(?!\d)", text) for c in candidates)


async def extract_stock_movements(text: str) -> list[dict]:
    """One Groq call over the whole message. Returns every stock movement that
    both the LLM extracted AND whose quantity is independently verified to
    appear literally in the source text."""
    try:
        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=800,
        )
        parsed = json.loads(completion.choices[0].message.content)
        movements = parsed.get("movements", [])
        if not isinstance(movements, list):
            return []
    except Exception as e:
        print(f"⚠️  Stock extraction failed: {e}")
        return []

    verified = []
    for m in movements:
        direction = m.get("direction")
        item = m.get("item")
        quantity = m.get("quantity")
        if direction not in ("in", "out") or not item or quantity is None:
            continue
        if not _quantity_verified_in_text(quantity, text):
            print(f"⚠️  Rejected stock movement (quantity not found verbatim in message): {m}")
            continue
        verified.append({
            "direction": direction,
            "item": str(item).strip().lower(),
            "quantity": float(quantity),
            "unit": m.get("unit"),
        })
    return verified


async def parse_stock_message(text: str) -> dict | None:
    """Returns None if the message doesn't even look like a stock-command
    attempt (falls through to normal ledger/intent handling). Otherwise
    {"commands": [...verified movements...], "attempted": <count of lines that
    looked like an attempt>} — a gap between the two counts means some lines
    couldn't be confidently read or failed verification."""
    if not text:
        return None
    attempted = len(STOCK_ATTEMPT_RE.findall(text))
    if not attempted:
        return None
    commands = await extract_stock_movements(text)
    return {"commands": commands, "attempted": attempted}


def is_report_command(text: str) -> bool:
    """True if the message is the literal 'REPORT' command (case-sensitive)."""
    return bool(text) and text.strip().startswith("REPORT")
