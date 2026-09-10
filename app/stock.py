"""
Stock command parsing — IN / OUT / REPORT, a deliberately rigid fixed-syntax
command language, separate from the free-text ledger entries in extract.py.

Case-sensitive by design: the fast path only fires on literal uppercase
"IN"/"OUT"/"REPORT" as the first token, so ordinary lowercase sentences
("in the evening we...") never get misrouted into stock parsing.

Quantity is always extracted by regex, never by the LLM — the only thing
Groq ever touches is the item name, purely to spell-correct it. This keeps
the number that actually drives the stock balance free of hallucination risk
while still getting typo tolerance on item names ("cemnt" -> "cement").
"""

import re
import json
from groq import Groq
from app.extract import groq_client as _shared_client

groq_client: Groq = _shared_client

STOCK_RE = re.compile(r"^(IN|OUT)\s+(.+?)\s+(\d+(?:\.\d+)?)\s*(\S+)?$")

SPELLING_SYSTEM_PROMPT = """You correct the spelling of a single inventory item name from a
WhatsApp stock-tracking bot (e.g. "cemnt" -> "cement", "stel rods" -> "steel rods").
Keep it lowercase, keep multi-word names as-is if already correct, never change its meaning
or guess a different item. Return ONLY a JSON object: {"item": "<corrected name>"}
"""

LLM_SYSTEM_PROMPT = """You extract a stock movement command from a WhatsApp message that
clearly starts with "IN" or "OUT" but doesn't follow the exact expected format
"IN <item> <quantity> <unit>" (e.g. it has extra words or reordered fields).

Return ONLY a JSON object of this exact shape:
{
  "direction": "in" | "out",
  "item": "<item name, lowercase>" | null,
  "quantity": <number> | null,
  "unit": "<unit if mentioned>" | null
}

Only extract a quantity if a literal numeral appears in the message — never convert a
spelled-out number ("fifty") into a digit, and never guess a quantity that isn't clearly
present. If you can't confidently identify all of direction, item, and quantity, set the
missing field(s) to null. Return ONLY the JSON object, no markdown, no explanation.
"""


async def correct_item_spelling(raw_item: str) -> str:
    """Spell-corrects an item name via Groq. Falls back to the raw text on any failure —
    a spelling miss is harmless, unlike a wrong quantity."""
    try:
        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": SPELLING_SYSTEM_PROMPT},
                {"role": "user", "content": raw_item},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=300,
        )
        parsed = json.loads(completion.choices[0].message.content)
        corrected = parsed.get("item")
        return str(corrected).strip().lower() if corrected else raw_item.strip().lower()
    except Exception as e:
        print(f"⚠️  Item spelling correction failed, using raw text: {e}")
        return raw_item.strip().lower()


async def parse_stock_command(text: str) -> dict | None:
    """Regex extracts direction/quantity/unit deterministically; the item name alone
    is spell-corrected via Groq. Returns {direction, item, quantity, unit} or None if
    the message doesn't match the strict IN/OUT format at all."""
    if not text:
        return None
    match = STOCK_RE.match(text.strip())
    if not match:
        return None
    direction, raw_item, quantity, unit = match.groups()
    item = await correct_item_spelling(raw_item)
    return {
        "direction": direction.lower(),
        "item": item,
        "quantity": float(quantity),
        "unit": unit,
    }


async def parse_stock_command_llm(text: str) -> dict | None:
    """Full LLM fallback for messages that start with 'IN '/'OUT ' but don't match the
    strict format at all (e.g. reordered or with extra words) — only reached when the
    regex path above can't even locate a numeral quantity."""
    try:
        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=500,
        )
        parsed = json.loads(completion.choices[0].message.content)
        if (
            parsed.get("direction") in ("in", "out")
            and parsed.get("item")
            and parsed.get("quantity") is not None
        ):
            return {
                "direction": parsed["direction"],
                "item": str(parsed["item"]).strip().lower(),
                "quantity": float(parsed["quantity"]),
                "unit": parsed.get("unit"),
            }
        return None
    except Exception as e:
        print(f"⚠️  Stock LLM fallback failed: {e}")
        return None


def is_report_command(text: str) -> bool:
    """True if the message is the literal 'REPORT' command (case-sensitive)."""
    return bool(text) and text.strip().startswith("REPORT")
