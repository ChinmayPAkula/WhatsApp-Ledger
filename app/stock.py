"""
Stock command parsing — IN / OUT / REPORT, a deliberately rigid fixed-syntax
command language, separate from the free-text ledger entries in extract.py.

Matching is case-insensitive on the IN/OUT keyword (real usage isn't
consistently capitalized), but the requirement that the *rest* of the line
end in "<number> [unit]" is what actually guards against collisions with
ordinary sentences — a normal message starting with "in "/"out " essentially
never also ends in a bare quantity.

A message can contain multiple stock lines (workers often log several items
in one WhatsApp message, one per line) — each line starting with in/out is
parsed independently.

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

STOCK_LINE_RE = re.compile(r"^(IN|OUT)\s+(.+?)\s+(\d+(?:\.\d+)?)\s*(\S+)?$", re.IGNORECASE)
# Used only to decide whether a line is even an *attempt* at a stock command
# (so a failed one gets a usage hint instead of silently vanishing). Requires
# a digit too — "in the evening we ran out" starts with "in " but has no
# quantity, so it correctly falls through to normal handling instead of
# getting flagged as a malformed stock command.
STOCK_ATTEMPT_RE = re.compile(r"^(IN|OUT)\s.*\d", re.IGNORECASE)

SPELLING_SYSTEM_PROMPT = """You correct the spelling of a single inventory item name from a
WhatsApp stock-tracking bot (e.g. "cemnt" -> "cement", "stel rods" -> "steel rods").
Keep it lowercase, keep multi-word names as-is if already correct, never change its meaning
or guess a different item. Return ONLY a JSON object: {"item": "<corrected name>"}
"""

LLM_SYSTEM_PROMPT = """You extract a stock movement command from one line of a WhatsApp
message that clearly starts with "IN" or "OUT" but doesn't follow the exact expected format
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


async def parse_stock_command(line: str) -> dict | None:
    """Regex extracts direction/quantity/unit deterministically from a single line;
    the item name alone is spell-corrected via Groq. Returns
    {direction, item, quantity, unit} or None if the line doesn't match at all."""
    if not line:
        return None
    match = STOCK_LINE_RE.match(line.strip())
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


async def parse_stock_command_llm(line: str) -> dict | None:
    """Full LLM fallback for a line that starts with 'IN '/'OUT ' but doesn't match the
    strict format at all (e.g. reordered or with extra words) — only reached when the
    regex path above can't even locate a numeral quantity on that line."""
    try:
        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": line},
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


async def parse_stock_message(text: str) -> dict | None:
    """Splits a message into lines and parses every line that looks like a stock
    command (starts with in/out, case-insensitive). Returns None if no line even
    looks like an attempt at one — lets the caller fall through to normal ledger/
    intent handling. Otherwise returns {"commands": [...], "unparsed": [raw lines
    that started with in/out but couldn't be parsed by either regex or LLM]}."""
    if not text:
        return None
    stock_lines = [line.strip() for line in text.splitlines() if STOCK_ATTEMPT_RE.match(line.strip())]
    if not stock_lines:
        return None

    commands = []
    unparsed = []
    for line in stock_lines:
        cmd = await parse_stock_command(line)
        if cmd is None:
            cmd = await parse_stock_command_llm(line)
        if cmd:
            commands.append(cmd)
        else:
            unparsed.append(line)

    return {"commands": commands, "unparsed": unparsed}


def is_report_command(text: str) -> bool:
    """True if the message is the literal 'REPORT' command (case-sensitive)."""
    return bool(text) and text.strip().startswith("REPORT")
