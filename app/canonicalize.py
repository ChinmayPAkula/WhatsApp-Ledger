"""
Deterministic item-name canonicalization — fuzzy-matches a freshly extracted
item name against names already stored in the database, and snaps to the
existing spelling on a close match.

No LLM call: pure string similarity (difflib, stdlib), so it's fast, free,
and gives the same result every time for the same input — which a second
LLM check can't guarantee, since it's subject to the same sampling variance
that caused the drift in the first place ("masal puri" vs "masala puri" from
two calls on the same model). The first time an item appears, whatever
spelling comes out becomes canonical; later mentions converge onto it
instead of drifting further.
"""

import difflib
from app.database import supabase

CUTOFF = 0.82


async def get_known_items(table: str) -> list[str]:
    """Distinct item names already stored in the given table."""
    result = supabase.table(table).select("item").execute()
    seen = set()
    items = []
    for row in result.data:
        item = row.get("item")
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    return items


def canonicalize(candidate: str, known_items: list[str]) -> str:
    """Returns the closest existing item name on a close-enough match,
    otherwise the candidate unchanged."""
    if not candidate or not known_items:
        return candidate
    by_lower = {k.lower(): k for k in known_items}
    matches = difflib.get_close_matches(candidate.lower(), list(by_lower.keys()), n=1, cutoff=CUTOFF)
    return by_lower[matches[0]] if matches else candidate
