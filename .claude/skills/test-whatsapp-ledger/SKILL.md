---
name: test-whatsapp-ledger
description: Run the full local test suite for the WhatsApp Ledger backend — module imports, deterministic parsing, live Groq calls, live Supabase/report generation, and a full signed-webhook run (signature validation, rate limiting, API key gate, entry/stock/malformed flows). Use when asked to test this project, run its tests, verify a change didn't break anything, or before pushing/deploying.
---

# Test WhatsApp Ledger

Runs `test_all.py` at the repo root, which exercises the whole backend end-to-end against **live** Groq and Supabase (no mocks) and reports every check as ✓/✗.

## When to use this
- After changing anything in `app/` (webhook routing, intent classification, extraction, stock parsing, report generation).
- Before committing/pushing a change, or before telling the user something is "ready to deploy."
- When the user asks to "test everything," "run the tests," or "make sure nothing broke."

## How to run it
```
cd <repo root>
python test_all.py
```
On Windows with the project's venv:
```
./venv/Scripts/python.exe test_all.py
```
Use `PYTHONIOENCODING=utf-8` in the environment if running on Windows — several log lines use emoji and the default console codepage can crash on them (this is purely a console-encoding issue, not a real failure).

## What it covers
1. **Imports** — every `app/*` module loads without error.
2. **Deterministic parsing** — stock command regex (well-formed, malformed, and the lowercase-collision case), `is_report_command`, `resolve_period` for all three period tokens.
3. **Live Groq calls** — `classify_intent` for entry/report_request/other, `extract_entries` (vendor extraction), `parse_stock_command` (spelling correction, exact quantity), `parse_stock_command_llm` fallback (reworded command, and gibberish correctly rejected).
4. **Live Supabase + reports** — `generate_report_workbook` and `generate_stock_report_workbook` both produce valid, correctly-sheeted `.xlsx` files against real data; a Storage upload + signed URL round-trip.
5. **Live webhook** — spins up a real `uvicorn` subprocess on port 8721 and sends real signed (and deliberately forged) HTTP requests: signature rejection, an entry message end-to-end, stock IN/OUT with balance math, a malformed stock command, the `REPORT` command's immediate ack, the `X-API-Key` gate on `/messages`, and rate limiting past 20/min.
6. **Cleanup** — deletes every row written under the synthetic test sender (`919999999999`, never a real device) and any report file regenerated during the run. Runs even if an earlier check fails.

## Reading the output
- Ends with `N passed, M failed` and a list of failed check names if any.
- Exits non-zero on any failure — safe to use as a pre-push gate.
- A failure in section 5 (`REPORT command acks...`) does NOT mean the actual report delivery is broken — that check only verifies the synchronous acknowledgment reply; the background generation/Twilio-send happens for real but isn't asserted on (verified manually against a real device instead, since asserting on a real WhatsApp delivery isn't practical in an automated run).

## What this does NOT verify
- Actual delivery of a report to a real WhatsApp device (verify manually if that path changed).
- The monthly cron script (`app/monthly_report.py`) — run it manually (`python -m app.monthly_report`) if you need to check it, since it always sends real messages to `OWNER_WHATSAPP_TO`.
- Anything about the deployed Render environment — this only tests the local checkout.
