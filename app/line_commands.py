"""
Shared machinery for "header + item lines" commands ("Order:", "Item Plan:"):
a deterministic regex gate, one Groq call to extract the lines, a verbatim
cross-check of every quantity against the raw text, unit normalization, and
canonicalization of item names against names already stored.

stock.py has its own variant because stock lines also carry a direction.
"""

import re
import json
from groq import Groq
from app.extract import groq_client as _shared_client
from app.canonicalize import get_known_items, canonicalize
from app.units import normalize_quantity
from app.stock import STOCK_HEADER_RE, OTHER_HEADER_RE, _quantity_verified_in_text

groq_client: Groq = _shared_client


def build_gate(keyword_pattern: str) -> tuple[re.Pattern, re.Pattern]:
    """(header_re, inline_re) for a command keyword such as r"ORDER" or
    r"ITEM[ \\t]+PLAN". The colon is required for the inline form
    ("Order: Milk 2 ltr") so an ordinary sentence starting with the keyword never
    matches; a bare keyword is only accepted alone on its own line, as a header."""
    header = re.compile(rf"^[ \t]*{keyword_pattern}[ \t]*:?[ \t]*$", re.IGNORECASE)
    inline = re.compile(rf"^[ \t]*{keyword_pattern}[ \t]*:[ \t]*\S.*\d", re.IGNORECASE)
    return header, inline


def count_attempts(text: str, header_re: re.Pattern, inline_re: re.Pattern) -> int:
    """How many lines look like an attempted item: every inline command, plus
    every item line (one containing a digit) under the header. The block ends at
    the next header of any kind."""
    attempted = 0
    under_header = False
    for line in text.splitlines():
        if header_re.match(line):
            under_header = True
        elif inline_re.match(line):
            attempted += 1
            under_header = False
        elif STOCK_HEADER_RE.match(line) or OTHER_HEADER_RE.match(line):
            under_header = False
        elif under_header and re.search(r"\d", line):
            attempted += 1
    return attempted


async def extract_lines(text: str, system_prompt: str, known_items_table: str, label: str) -> list[dict]:
    """One Groq call over the whole message. Returns only items whose quantity
    is independently verified to appear literally in the source text."""
    try:
        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": system_prompt},
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
        print(f"⚠️  {label} extraction failed: {e}")
        return []

    verified = []
    for m in items:
        item, quantity = m.get("item"), m.get("quantity")
        if not item or quantity is None:
            continue
        if not _quantity_verified_in_text(quantity, text):
            print(f"⚠️  Rejected {label} item (quantity not found verbatim in message): {m}")
            continue
        qty, unit, _ = normalize_quantity(float(quantity), m.get("unit"))
        verified.append({"item": str(item).strip().lower(), "quantity": qty, "unit": unit})

    if verified:
        known_items = await get_known_items(known_items_table)
        for entry in verified:
            canonical = canonicalize(entry["item"], known_items)
            if canonical != entry["item"]:
                print(f"   📎 Canonicalized {label} item: {entry['item']!r} -> {canonical!r}")
            entry["item"] = canonical

    return verified


async def parse_lines(text: str, header_re: re.Pattern, inline_re: re.Pattern,
                      system_prompt: str, known_items_table: str, label: str) -> dict | None:
    """None if the message isn't an attempt at this command (falls through to
    normal handling). Otherwise {"items": [...verified...], "attempted": <count of
    lines that looked like an attempt>}."""
    if not text:
        return None
    attempted = count_attempts(text, header_re, inline_re)
    if not attempted:
        return None
    return {"items": await extract_lines(text, system_prompt, known_items_table, label), "attempted": attempted}
