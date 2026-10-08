"""
Item Plan command parsing — "Item Plan:" followed by the dishes being prepared
for the day and how many of each, e.g. "Paneer Cutlet 35 pcs". Same gate,
extraction and quantity cross-check as "Order:" (line_commands.py), saved to
its own item_plans table.
"""

from app.line_commands import build_gate, count_attempts, extract_lines, parse_lines

PLAN_HEADER_RE, PLAN_INLINE_RE = build_gate(r"ITEM[ \t]+PLAN")

EXTRACT_SYSTEM_PROMPT = """You extract the day's preparation plan from a WhatsApp message sent to
a kitchen bot. The message starts with "Item Plan:" (any capitalization) and lists the dishes
being prepared and how many of each, one per line, e.g.:

Item Plan:
Corn Cheese Balls 30 pcs
Potato Croquettes 25 pcs

or on one line: "Item Plan: Paneer Cutlet 35 pcs".

For each dish line, extract:
- item: the dish name, spelling-corrected and lowercase (keep multi-word names whole)
- quantity: the number, EXACTLY as written in the message — never invent, round, or convert a
  spelled-out word into a digit; if no literal numeral is present for a line, omit that line
- unit: the unit if mentioned (e.g. "pcs", "kg"), else null

Return ONLY a JSON object of this exact shape:
{"items": [{"item": "<name>", "quantity": <number>, "unit": "<unit>"|null}, ...]}

Ignore the "Item Plan:" header itself and any line that isn't clearly a dish. Return ONLY the
JSON object, no markdown, no explanation.
"""


def count_plan_attempts(text: str) -> int:
    return count_attempts(text, PLAN_HEADER_RE, PLAN_INLINE_RE)


async def extract_plan_items(text: str) -> list[dict]:
    return await extract_lines(text, EXTRACT_SYSTEM_PROMPT, "item_plans", "item plan")


async def parse_plan_message(text: str) -> dict | None:
    """None if the message isn't an item-plan attempt (falls through to normal
    handling), else {"items": [...verified...], "attempted": <line count>}."""
    return await parse_lines(text, PLAN_HEADER_RE, PLAN_INLINE_RE, EXTRACT_SYSTEM_PROMPT, "item_plans", "item plan")


def format_plan_line(item: dict) -> str:
    return f"{item['item'].title()} {item['quantity']:g}{item.get('unit') or ''}"
