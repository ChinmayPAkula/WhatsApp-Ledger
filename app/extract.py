"""
AI extraction module — takes raw WhatsApp message text and returns
a list of structured entries using Groq's Llama 3.3 70B model.

Each message can contain multiple items, so we always return a list of entries.
Nothing is required to be present, if the AI can't find a field, it stays null.
"""

import os
import json
from datetime import datetime
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Categories the AI must pick from. AI should always pick one or "Other".
CATEGORIES = [
    "Dairy",
    "Vegetables",
    "Syrups & Crushes",
    "Utensils",
    "Gas",
    "Oil",
    "Plastics",
    "Grocery",
    "Other",
]

SYSTEM_PROMPT = f"""You are an extraction assistant for a fast food restaurant owner in India.
The owner sends WhatsApp messages about ORDERS he places with vendors and DELIVERIES that arrive.
Your job: parse each message into structured JSON entries.

CATEGORIES (pick one for each item, or "Other"):
{", ".join(CATEGORIES)}

OUTPUT FORMAT — ALWAYS return a JSON object with this exact shape:
{{
  "entries": [
    {{
      "entry_type": "order" | "delivery" | "unclear",
      "item": "<spell-corrected English name, lowercase>" | null,
      "quantity": <number> | null,
      "unit": "kg" | "g" | "l" | "ml" | "piece" | "tin" | "cylinder" | "packet" | "bunch" | "dozen" | "box" | null,
      "category": "<one of the categories above>" | null,
      "price_per_unit": <number in rupees> | null,
      "total_price": <number in rupees> | null,
      "vendor": "<vendor/supplier name if mentioned>" | null,
      "notes": "<any extra context>" | null,
      "status": "confirmed" | "unclear"
    }}
  ]
}}

EXTRACTION RULES:
1. Multiple items in one message = multiple entries (split them into separate rows).
2. Spell-correct item names. Examples:
   - "tamoto" → "tomato"
   - "onin" → "onion"
   - "dhana" / "dhania" → "coriander"
   - "pyaaz" → "onion"
   - "aalu" / "aloo" → "potato"
   - "paneer" → "paneer"
   - "haldi" → "turmeric"
3. Detect ORDER vs DELIVERY from context:
   - ORDER signals: future-tense ("need", "want", "tomorrow"), plain item list with quantities, no past-tense verbs
   - DELIVERY signals: "received", "got", "delivered", "arrived", "reached", "bill", price mentioned
   - If genuinely unclear: entry_type="unclear", status="unclear"
4. Price formats:
   - "@40" or "@40/kg" or "₹40/kg" → price_per_unit = 40
   - "bill 250" or "paid 200" → total_price = 250
   - "5kg @40" → quantity=5, unit="kg", price_per_unit=40, you may also compute total_price=200
5. Units: detect from text. "5kg" → quantity=5, unit="kg". If no unit given, leave unit null.
5b. Vendor: if a supplier/vendor name is mentioned (e.g. "from babu anna", "raju vegetables", a shop name), set vendor to that name (spell-corrected, title case as written). If no vendor is named, leave vendor null. Never guess a vendor from the sender's own phone number.
6. If the message is NOT about orders/deliveries (e.g. "ok thanks", "good morning"):
   - Return one entry with entry_type="unclear", item=null, status="unclear", notes="<original text>"
7. NEVER make up information. If a field isn't in the message, leave it null.
8. Be liberal — if you have a reasonable guess, fill it in. Only mark status="unclear" when truly ambiguous.

Return ONLY the JSON object. No markdown, no code fences, no explanation.
"""


async def extract_entries(message_text: str, message_timestamp: str = None) -> list[dict]:
    """
    Call Groq to extract structured entries from a WhatsApp message.
    Returns a list of entry dicts. Empty list if extraction completely failed.
    """
    if not message_text or not message_text.strip():
        return []

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    user_prompt = f"Current date/time (UTC): {now}\n\nMessage to parse:\n{message_text}"

    try:
        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=2000,
        )

        raw = completion.choices[0].message.content
        print(f"🤖 Groq raw response: {raw}")

        parsed = json.loads(raw)
        entries = parsed.get("entries", [])

        if not isinstance(entries, list):
            print(f"⚠️  Groq returned non-list entries: {entries}")
            return []

        return entries

    except json.JSONDecodeError as e:
        print(f"⚠️  Groq returned invalid JSON: {e}")
        return []
    except Exception as e:
        print(f"⚠️  Groq extraction failed: {e}")
        return []
