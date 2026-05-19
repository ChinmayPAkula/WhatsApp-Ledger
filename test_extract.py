"""
Quick local test for Groq extraction.
Run with:  python test_extract.py
This does NOT touch the database — it only tests the AI parsing.
"""

import asyncio
from app.extract import extract_entries


TEST_MESSAGES = [
    "tomato 5kg, onion 10kg, dhana 2 bunch",
    "received tomato 5kg @40",
    "gas 1 cylinder from babu anna",
    "paneer 2kg @350 delivered today",
    "need 5kg cooking oil tomorrow",
    "tamoto, onin, dhana",
    "cooking oil 2 tin @1800/tin received",
    "paper plates 2 packets, paper cups 5 packets",
    "ghee 1 tin",
    "ok thanks",
]


async def main():
    for i, msg in enumerate(TEST_MESSAGES, 1):
        print("=" * 70)
        print(f"TEST {i}: {msg!r}")
        print("-" * 70)
        entries = await extract_entries(msg)
        if not entries:
            print("  (no entries extracted)")
        for e in entries:
            print(f"  type={e.get('entry_type'):10} "
                  f"item={str(e.get('item')):15} "
                  f"qty={str(e.get('quantity')):6} "
                  f"unit={str(e.get('unit')):8} "
                  f"cat={str(e.get('category')):16} "
                  f"₹/u={str(e.get('price_per_unit')):6} "
                  f"total={str(e.get('total_price')):6} "
                  f"status={e.get('status')}")
            if e.get("notes"):
                print(f"           notes: {e.get('notes')}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
