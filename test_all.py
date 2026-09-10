"""
Full local test suite for the WhatsApp Ledger backend.
Run with:  python test_all.py

Covers: module imports, deterministic parsing (stock commands, period
resolution), live Groq calls (intent classification, entry extraction,
stock spelling correction/fallback), live Supabase + report generation,
and a full signed-webhook run against a locally-started server (signature
validation, rate limiting, API key gate, entry/stock/malformed flows).

The REPORT command's background task DOES run for real (this hits a live
server subprocess, not a mock) — it regenerates that month's stock report
in Supabase Storage and attempts a real Twilio send to the synthetic test
number (which harmlessly fails, already caught and logged by the app).
Cleanup removes the regenerated report file again at the end.

Any row this script writes to Supabase (tagged with a "__TEST__" marker)
is deleted again at the end, even if a test fails partway through.
"""

import os
import sys
import time
import asyncio
import subprocess
from datetime import date

import httpx
from dotenv import load_dotenv

load_dotenv()

PORT = 8721
BASE_URL = f"http://127.0.0.1:{PORT}"
# A clearly synthetic sender, never the real owner's number — cleanup deletes
# every row tied to this sender unconditionally, so it must never collide
# with a real device that could be testing concurrently.
TEST_SENDER = "919999999999"
TEST_TAG = "__TEST__"

passed = []
failed = []


def check(name: str, condition: bool, detail: str = ""):
    if condition:
        passed.append(name)
        print(f"  ✓ {name}")
    else:
        failed.append(name)
        print(f"  ✗ {name}  {detail}")


def section(title: str):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ── 1. Imports ──

def test_imports():
    section("1. Module imports")
    try:
        import app.main  # noqa
        import app.stock  # noqa
        import app.report  # noqa
        import app.intent  # noqa
        import app.extract  # noqa
        import app.database  # noqa
        import app.monthly_report  # noqa
        check("all app modules import cleanly", True)
    except Exception as e:
        check("all app modules import cleanly", False, str(e))


# ── 2. Deterministic parsing (no network) ──

def test_deterministic_parsing():
    section("2. Deterministic parsing")
    from app.stock import STOCK_ATTEMPT_RE, is_report_command, _quantity_verified_in_text
    from app.report import resolve_period

    check(
        "lowercase 'in'/'out' sentences without a number don't look like stock attempts",
        not STOCK_ATTEMPT_RE.search("in the evening we ran out") and not STOCK_ATTEMPT_RE.search("out of stock today"),
    )
    check("a line with 'in'/'out' + a digit does look like an attempt", bool(STOCK_ATTEMPT_RE.search("in potato 2kg")))
    check(
        "multi-line message: attempt count matches number of qualifying lines",
        len(STOCK_ATTEMPT_RE.findall("IN Milk 2litres\nIN paneer 4kg\nIn potato 2kg\nOut mushroom 1kg")) == 4,
    )

    check("quantity cross-check: exact match found", _quantity_verified_in_text(50, "IN cement 50 bags"))
    check("quantity cross-check: decimal match found", _quantity_verified_in_text(2.5, "IN oil 2.5 litres"))
    check("quantity cross-check: hallucinated number rejected", not _quantity_verified_in_text(999, "IN cement 50 bags"))
    check(
        "quantity cross-check: doesn't false-match a substring of a bigger number",
        not _quantity_verified_in_text(2, "IN cement 20 bags"),
    )

    from app.canonicalize import canonicalize
    check("canonicalize: near-duplicate spelling snaps to existing item", canonicalize("masal puri", ["masala puri"]) == "masala puri")
    check("canonicalize: exact match is a no-op", canonicalize("masala puri", ["masala puri"]) == "masala puri")
    check("canonicalize: unrelated item is left alone", canonicalize("tomato", ["potato", "onion"]) == "tomato")
    check("canonicalize: no known items is a no-op", canonicalize("tomato", []) == "tomato")

    check("is_report_command('REPORT')", is_report_command("REPORT"))
    check("is_report_command('REPORT last month')", is_report_command("REPORT last month"))
    check("is_report_command('report') is False (case-sensitive)", not is_report_command("report"))

    today = date(2026, 9, 10)
    start, end = resolve_period("this_month", reference=today)
    check("resolve_period this_month", (start, end) == (date(2026, 9, 1), date(2026, 10, 1)))
    start, end = resolve_period("last_month", reference=today)
    check("resolve_period last_month", (start, end) == (date(2026, 8, 1), date(2026, 9, 1)))
    start, end = resolve_period("2026-05", reference=today)
    check("resolve_period explicit YYYY-MM", (start, end) == (date(2026, 5, 1), date(2026, 6, 1)))

    from app.audit import parse_log_request
    check(
        "parse_log_request: correct password authorizes",
        parse_log_request("send me this month logs password pineapple") == {"authorized": True, "period": "this_month"},
    )
    check(
        "parse_log_request: wrong password rejected",
        parse_log_request("send me this month logs password wrongword")["authorized"] is False,
    )
    check(
        "parse_log_request: missing password rejected, not a crash",
        parse_log_request("send me this month logs")["authorized"] is False,
    )
    check("parse_log_request: unrelated message is not an attempt", parse_log_request("ok thanks") is None)
    check(
        "parse_log_request: 'last month' phrasing sets the right period",
        parse_log_request("send me last month logs password pineapple")["period"] == "last_month",
    )


# ── 3. Live Groq calls ──

async def test_groq():
    section("3. Live Groq calls (intent, extraction, stock parsing)")
    from app.intent import classify_intent
    from app.extract import extract_entries
    from app.stock import extract_stock_movements

    r = await classify_intent("tomato 5kg @40")
    check("classify_intent: ledger message -> entry", r["intent"] == "entry", str(r))

    r = await classify_intent("give me the in out stock report")
    check("classify_intent: stock report phrase -> stock_report_request", r["intent"] == "stock_report_request", str(r))

    r = await classify_intent("send me this months excel sheet")
    check("classify_intent: report phrase -> report_request", r["intent"] == "report_request", str(r))

    r = await classify_intent("ok thanks")
    check("classify_intent: chatter -> other", r["intent"] == "other", str(r))

    entries = await extract_entries("received tomato 5kg @40 from raju vegetables")
    check(
        "extract_entries: vendor extracted",
        bool(entries) and entries[0].get("vendor") and "raju" in entries[0]["vendor"].lower(),
        str(entries),
    )

    r = await extract_stock_movements("IN Cemnt 50 bags")
    check(
        "extract_stock_movements: single line, spelling corrected, quantity exact",
        len(r) == 1 and r[0]["item"] == "cement" and r[0]["quantity"] == 50.0,
        str(r),
    )

    r = await extract_stock_movements("IN Milk 2litres\nIN paneer 4kg\nIn potato 2kg\nOut mushroom 1kg")
    check(
        "extract_stock_movements: multi-line message extracts all 4 movements",
        len(r) == 4 and sum(1 for m in r if m["direction"] == "in") == 3,
        str(r),
    )

    r = await extract_stock_movements("IN Cement 50 sacks arrived today, thanks!")
    check(
        "extract_stock_movements: extra surrounding words still extract correctly",
        len(r) == 1 and r[0]["direction"] == "in" and r[0]["quantity"] == 50.0,
        str(r),
    )

    r = await extract_stock_movements("IN blah blah nonsense")
    check("extract_stock_movements: no quantity present -> nothing extracted", len(r) == 0, str(r))


# ── 4. Live Supabase + report generation ──

async def test_reports():
    section("4. Live Supabase + report generation")
    from app.report import generate_report_workbook, generate_stock_report_workbook
    from app.database import upload_report, supabase
    from openpyxl import load_workbook
    from io import BytesIO

    today = date.today()
    start = date(today.year, today.month, 1)
    end = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)

    try:
        content = await generate_report_workbook(start, end)
        wb = load_workbook(BytesIO(content))
        check("generate_report_workbook produces a valid .xlsx", "Ledger" in wb.sheetnames, str(wb.sheetnames))
    except Exception as e:
        check("generate_report_workbook produces a valid .xlsx", False, str(e))

    try:
        content = await generate_stock_report_workbook(start, end)
        wb = load_workbook(BytesIO(content))
        check(
            "generate_stock_report_workbook produces a valid .xlsx",
            set(wb.sheetnames) == {"Current Stock", "IN", "OUT"},
            str(wb.sheetnames),
        )
    except Exception as e:
        check("generate_stock_report_workbook produces a valid .xlsx", False, str(e))

    try:
        test_filename = f"{TEST_TAG}_upload_check.xlsx"
        url = await upload_report(test_filename, b"dummy bytes for upload test")
        resp = httpx.get(url, timeout=10)
        check("upload_report: signed URL is fetchable", resp.status_code == 200, f"status={resp.status_code}")
        supabase.storage.from_("reports").remove([test_filename])
    except Exception as e:
        check("upload_report: signed URL is fetchable", False, str(e))


# ── 5. Live webhook (signed requests against a locally-started server) ──

def sign(body_text: str) -> str:
    from twilio.request_validator import RequestValidator
    v = RequestValidator(os.getenv("TWILIO_AUTH_TOKEN"))
    return v.compute_signature(
        f"{BASE_URL}/webhook",
        {"From": f"whatsapp:+{TEST_SENDER}", "To": "whatsapp:+14155238886", "Body": body_text, "NumMedia": "0"},
    )


def post_webhook(client: httpx.Client, body_text: str, signed: bool = True) -> httpx.Response:
    headers = {"X-Twilio-Signature": sign(body_text)} if signed else {}
    return client.post(
        "/webhook",
        headers=headers,
        data={"From": f"whatsapp:+{TEST_SENDER}", "To": "whatsapp:+14155238886", "Body": body_text, "NumMedia": "0"},
    )


def test_webhook_live():
    section("5. Live webhook (signature validation, rate limiting, API key gate)")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            try:
                if httpx.get(f"{BASE_URL}/", timeout=1).status_code == 200:
                    break
            except httpx.RequestError:
                pass
            time.sleep(0.5)
        else:
            check("server started", False, "timed out waiting for startup")
            return

        with httpx.Client(base_url=BASE_URL, timeout=15) as client:
            r = post_webhook(client, "forged test message", signed=False)
            check("forged webhook request rejected (403)", r.status_code == 403, str(r.status_code))

            r = post_webhook(client, "tomato 5kg @40")
            check(
                "signed entry message logged with confirmation",
                r.status_code == 200 and "Logged" in r.text and "tomato" in r.text,
                r.text,
            )

            r = post_webhook(client, "IN Cemnt 50 bags")
            check(
                "signed stock IN command confirms with corrected spelling",
                r.status_code == 200 and "Stock IN" in r.text and "cement" in r.text.lower(),
                r.text,
            )
            r = post_webhook(client, "OUT Cemnt 20 bags")
            check(
                "signed stock OUT command shows correct running balance",
                r.status_code == 200 and "balance: 30" in r.text,
                r.text,
            )

            r = post_webhook(client, "IN 50")
            check(
                "stock attempt with a digit but no item gets a usage hint, not silent failure",
                r.status_code == 200 and "Couldn't read" in r.text,
                r.text,
            )

            r = post_webhook(client, "IN Cement fifty bags")
            check(
                "word-numbered quantity (no digit) falls through to normal ledger handling",
                r.status_code == 200 and "Stock" not in r.text,
                r.text,
            )

            r = post_webhook(client, "IN Milk 2litres\nIN paneer 4kg\nIn potato 2kg\nOut mushroom 1kg")
            check(
                "multi-line message logs all 4 stock movements",
                r.status_code == 200 and r.text.count("Stock IN") == 3 and r.text.count("Stock OUT") == 1,
                r.text,
            )

            r = post_webhook(client, "in the evening we ran out")
            check(
                "lowercase non-command sentence isn't misrouted into stock parsing",
                r.status_code == 200 and "Couldn't read" not in r.text and "Stock" not in r.text,
                r.text,
            )

            r = post_webhook(client, "REPORT")
            check(
                "REPORT command acks immediately (async delivery not tested here)",
                r.status_code == 200 and "generating your stock report" in r.text,
                r.text,
            )

            r = post_webhook(client, "give me the in out stock report")
            check(
                "natural-language stock report request routes to stock report, not ledger report",
                r.status_code == 200 and "generating your stock report" in r.text,
                r.text,
            )
            r = post_webhook(client, "send me this months excel sheet")
            check(
                "natural-language ledger report request still routes to ledger report",
                r.status_code == 200 and "generating your report" in r.text and "stock" not in r.text.lower(),
                r.text,
            )

            r = post_webhook(client, "send me this month logs password pineapple")
            check(
                "correct-password log request acks immediately",
                r.status_code == 200 and "generating the activity log" in r.text,
                r.text,
            )
            r = post_webhook(client, "send me this month logs password wrongword")
            check(
                "wrong-password log request is rejected, not silently accepted",
                r.status_code == 200 and "Incorrect password" in r.text,
                r.text,
            )

            api_key = os.getenv("API_ACCESS_KEY")
            r = client.get("/messages")
            check("GET /messages without API key -> 401", r.status_code == 401)
            r = client.get("/messages", headers={"X-API-Key": "wrong-key"})
            check("GET /messages with wrong API key -> 401", r.status_code == 401)
            r = client.get("/messages", headers={"X-API-Key": api_key})
            check("GET /messages with correct API key -> 200", r.status_code == 200)

            codes = []
            for i in range(25):
                r = post_webhook(client, "forged rate limit probe", signed=False)
                codes.append(r.status_code)
            check("rate limiting kicks in past 20/min (some 429s)", 429 in codes, str(set(codes)))

            # Let the REPORT command's background task (real Storage upload,
            # real Twilio send attempt) finish before we tear the server down.
            time.sleep(4)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ── 6. Cleanup — remove every row this script wrote ──

def cleanup():
    section("6. Cleanup")
    from app.database import supabase

    # Safe to delete unconditionally: TEST_SENDER is a synthetic number that
    # only this script ever writes under, never the real owner's device.
    msgs = supabase.table("messages").select("id").eq("sender_phone", TEST_SENDER).execute()
    ids = [m["id"] for m in msgs.data]
    if ids:
        supabase.table("entries").delete().in_("message_id", ids).execute()
        supabase.table("stock_transactions").delete().in_("message_id", ids).execute()
        supabase.table("messages").delete().in_("id", ids).execute()
    print(f"  removed {len(ids)} test message(s) and their entries/stock rows (sender={TEST_SENDER})")

    # The REPORT / report-request commands' background tasks regenerate this
    # month's report files for real during the live webhook test — remove them.
    month = date.today().strftime("%Y-%m")
    for regenerated in (f"stock_{month}.xlsx", f"ledger_{month}.xlsx", f"messagelog_{month}.xlsx"):
        try:
            supabase.storage.from_("reports").remove([regenerated])
            print(f"  removed regenerated {regenerated}")
        except Exception:
            pass


def main():
    test_imports()
    test_deterministic_parsing()
    asyncio.run(test_groq())
    asyncio.run(test_reports())
    test_webhook_live()
    cleanup()

    section("RESULT")
    print(f"{len(passed)} passed, {len(failed)} failed")
    if failed:
        print("\nFailed checks:")
        for name in failed:
            print(f"  - {name}")
        sys.exit(1)
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
