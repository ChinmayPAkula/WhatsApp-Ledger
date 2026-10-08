"""
Exact report keywords — "Stock Report", "Order Report", "Items Report",
optionally prefixed with "Get" (the request format in the requirements).

Deterministic, no LLM: the whole message must be the keyword, so ordinary
sentences that merely mention a report still go to the intent classifier.
"""

import re

REPORT_KEYWORD_RE = re.compile(
    r"^\s*(?:get\s+)?(stock|order|items?)\s+report(?:\s+(last|this)\s+month)?\s*[.!]?\s*$",
    re.IGNORECASE,
)


def match_report_keyword(text: str) -> dict | None:
    """Returns {"kind": "stock" | "order" | "items", "period": "this_month" |
    "last_month"} for an exact keyword message, else None. The period only
    means something for the stock report; the order report is all-time."""
    match = REPORT_KEYWORD_RE.match(text or "")
    if not match:
        return None
    kind = "items" if match.group(1).lower().startswith("item") else match.group(1).lower()
    period = "last_month" if (match.group(2) or "").lower() == "last" else "this_month"
    return {"kind": kind, "period": period}
