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
from datetime import date, datetime, timezone

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
    from app.stock import STOCK_ATTEMPT_RE, is_report_command, _quantity_verified_in_text, count_stock_attempts
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

    from app.stock import count_stock_attempts
    in_out = "In:\nMilk 2 ltr\nOnion 5 kg\nCurd 2 ltr\nTomato 2 kg\n\nOut:\nCurd 1 ltr\nMilk 1 ltr\nTomato 500 gms\nOnion 1.5 kg"
    check("header format: In:/Out: blocks count every item line", count_stock_attempts(in_out) == 8, str(count_stock_attempts(in_out)))
    check("header format: header without colon works", count_stock_attempts("OUT\nCurd 1 ltr") == 1)
    check("header format: inline 'In: Milk 2 ltr' counts once", count_stock_attempts("In: Milk 2 ltr") == 1)
    check("header format: legacy per-line format still counts", count_stock_attempts("IN Milk 2litres\nOut mushroom 1kg") == 2)
    check("header format: a header with no items is not an attempt", count_stock_attempts("In:") == 0)
    check("header format: Order: block after In: is not counted as stock", count_stock_attempts("In:\nMilk 2 ltr\nOrder:\nOnion 5 kg") == 1)
    check("header format: ordinary chat is not an attempt", count_stock_attempts("in the evening we ran out") == 0)

    from app.report import _format_ist, _sender_label
    check("report: UTC timestamp shown in IST", _format_ist("2026-10-07T10:00:00+00:00") == "2026-10-07 15:30")
    check("report: sender label with profile name", _sender_label({"sender_phone": "919876543210", "sender_name": "Ravi"}) == "Ravi (+919876543210)")
    check("report: sender label without profile name", _sender_label({"sender_phone": "919876543210", "sender_name": None}) == "+919876543210")

    from app.orders import count_order_attempts, format_last_ordered
    pdf_order = "Order:\nMilk 2 ltr\nOnion 5 kg\nCurd 2 ltr\nTomato 2 kg"
    check("orders: PDF 'Order:' block counts every item line", count_order_attempts(pdf_order) == 4, str(count_order_attempts(pdf_order)))
    check("orders: inline 'Order: Milk 2 ltr' counts once", count_order_attempts("Order: Milk 2 ltr") == 1)
    check("orders: header alone is not an attempt", count_order_attempts("Order:") == 0)
    check("orders: ordinary sentence starting with 'order' is not an attempt", count_order_attempts("order of 5 boxes arrived") == 0)
    check("orders: In: block after Order: is not counted as an order", count_order_attempts("Order:\nMilk 2 ltr\nIn:\nOnion 5 kg") == 1)
    check("orders: Order: lines are not mistaken for stock attempts", count_stock_attempts(pdf_order) == 0)
    first = format_last_ordered({"item": "milk", "quantity": 2.0, "unit": "ltr"}, None)
    check("orders: never-ordered item says first time", first == "Milk 2ltr — first time ordered", first)
    last = {"created_at": "2026-10-03T10:00:00+00:00", "sender_phone": "919876543210", "sender_name": "Ravi"}
    line = format_last_ordered({"item": "milk", "quantity": 2.0, "unit": "ltr"}, last, now=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc))
    check("orders: last-ordered line shows date, days ago and who", line == "Milk 2ltr — last ordered 03 Oct (5 days ago) by Ravi (+919876543210)", line)

    from app.units import normalize_quantity, canonical_unit
    check("units: 500 gms -> 0.5 kg with a conversion note", normalize_quantity(500, "gms") == (0.5, "kg", "500 gms"), str(normalize_quantity(500, "gms")))
    check("units: 250 g -> 0.25 kg", normalize_quantity(250, "g")[:2] == (0.25, "kg"))
    check("units: 1500 ml -> 1.5 ltr", normalize_quantity(1500, "ml")[:2] == (1.5, "ltr"))
    check("units: 'litres' respelled to ltr with no conversion note", normalize_quantity(2, "Litres") == (2, "ltr", None))
    check("units: 'kgs' respelled to kg", normalize_quantity(5, "kgs") == (5, "kg", None))
    check("units: unknown unit passes through lowercased", normalize_quantity(50, "Bags") == (50, "bags", None))
    check("units: missing unit stays None", normalize_quantity(3, None) == (3, None, None))
    check("units: canonical_unit respells but never converts", canonical_unit("Gms") == "kg" and canonical_unit("packets") == "packets" and canonical_unit(None) is None)

    from app.item_plan import count_plan_attempts, format_plan_line
    pdf_plan = "Item Plan:\nCorn Cheese Balls 30 pcs\nPotato Croquettes 25 pcs\nPaneer Cutlet 35 pcs\nPaneer Papdi 28 pcs"
    check("item plan: PDF block counts every dish line", count_plan_attempts(pdf_plan) == 4, str(count_plan_attempts(pdf_plan)))
    check("item plan: inline 'Item Plan: Paneer Cutlet 35 pcs' counts once", count_plan_attempts("Item Plan: Paneer Cutlet 35 pcs") == 1)
    check("item plan: header alone is not an attempt", count_plan_attempts("Item Plan:") == 0)
    check("item plan: 'Items Report' is not a plan attempt", count_plan_attempts("Items Report") == 0)
    check("item plan: Order: lines are not mistaken for plan or stock lines", count_plan_attempts(pdf_order) == 0)
    check("item plan: lines are not mistaken for orders or stock", count_order_attempts(pdf_plan) == 0 and count_stock_attempts(pdf_plan) == 0)
    check("item plan: an Order: block after a plan ends the plan block", count_plan_attempts("Item Plan:\nPaneer Cutlet 35 pcs\nOrder:\nMilk 2 ltr") == 1)
    check("item plan: reply line formatting", format_plan_line({"item": "corn cheese balls", "quantity": 30.0, "unit": "pcs"}) == "Corn Cheese Balls 30pcs")

    from app.report_keywords import match_report_keyword
    check("keywords: 'Order Report' -> order", (match_report_keyword("Order Report") or {}).get("kind") == "order")
    check("keywords: 'Get Order Report' (any case) -> order", (match_report_keyword("get ORDER report") or {}).get("kind") == "order")
    check("keywords: 'Get Stock Report' -> stock, this month", match_report_keyword("Get Stock Report") == {"kind": "stock", "period": "this_month"})
    check("keywords: 'Stock Report last month' -> stock, last month", match_report_keyword("Stock Report last month") == {"kind": "stock", "period": "last_month"})
    check("keywords: 'Items Report' and 'Item Report' -> items", all((match_report_keyword(t) or {}).get("kind") == "items" for t in ("Items Report", "get item report")))
    check("keywords: trailing full stop is tolerated", (match_report_keyword("Order Report.") or {}).get("kind") == "order")
    check("keywords: a sentence merely mentioning a report is not a keyword", match_report_keyword("please send the order report to me") is None)
    check("keywords: 'Order:' block is not a report keyword", match_report_keyword("Order:\nMilk 2 ltr") is None)

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
        parse_log_request("send me this month logs password pineapple")
        == {"authorized": True, "password_given": True, "period": "this_month"},
    )
    check(
        "parse_log_request: wrong (but given) password rejected, distinguished from missing",
        parse_log_request("send me this month logs password wrongword") == {"authorized": False, "password_given": True, "period": "this_month"},
    )
    check(
        "parse_log_request: missing password rejected, distinguished from wrong",
        parse_log_request("send me this month logs") == {"authorized": False, "password_given": False, "period": "this_month"},
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

    r = await extract_stock_movements("In:\nMilk 2 ltr\nOnion 5 kg\nCurd 2 ltr\nTomato 2 kg\n\nOut:\nCurd 1 ltr\nMilk 1 ltr\nTomato 500 gms\nOnion 1.5 kg")
    ins = sorted(m["item"] for m in r if m["direction"] == "in")
    outs = sorted(m["item"] for m in r if m["direction"] == "out")
    check(
        "extract_stock_movements: In:/Out: header blocks set direction for every line below",
        ins == ["curd", "milk", "onion", "tomato"] and outs == ["curd", "milk", "onion", "tomato"],
        str(r),
    )

    from app.item_plan import extract_plan_items
    r = await extract_plan_items("Item Plan:\nCorn Cheese Balls 30 pcs\nPotato Croquettes 25 pcs\nPaneer Cutlet 35 pcs\nPaneer Papdi 28 pcs")
    check(
        "extract_plan_items: PDF sample keeps multi-word dish names with exact quantities",
        sorted((m["item"], m["quantity"], m["unit"]) for m in r)
        == [("corn cheese balls", 30.0, "pcs"), ("paneer cutlet", 35.0, "pcs"), ("paneer papdi", 28.0, "pcs"), ("potato croquettes", 25.0, "pcs")],
        str(r),
    )

    from app.orders import extract_order_items
    r = await extract_order_items("Order:\nMilk 2 ltr\nOnion 5 kg\nCurd 2 ltr\nTomato 2 kg")
    check(
        "extract_order_items: PDF sample extracts 4 items with exact quantities",
        sorted((m["item"], m["quantity"]) for m in r) == [("curd", 2.0), ("milk", 2.0), ("onion", 5.0), ("tomato", 2.0)],
        str(r),
    )

    r = await extract_stock_movements("Out:\nTomato 500 gms\nOnion 1.5 kg")
    tomato = next((m for m in r if m["item"] == "tomato"), {})
    check(
        "extract_stock_movements: 500 gms is converted to 0.5 kg after the verbatim check",
        tomato.get("quantity") == 0.5 and tomato.get("unit") == "kg" and tomato.get("converted_from") == "500 gms",
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
        content = await generate_stock_report_workbook(start, end, requested_by="+919999999999")
        ws = load_workbook(BytesIO(content))["Current Stock"]
        check(
            "stock report: Current Stock shows generation time and requester",
            "requested by +919999999999" in str(ws["A1"].value) and "IST" in str(ws["A1"].value),
            str(ws["A1"].value),
        )
        check(
            "stock report: Current Stock has Last Updated / Last Updated By columns",
            [c.value for c in ws[3]][5:7] == ["Last Updated", "Last Updated By"],
            str([c.value for c in ws[3]]),
        )
        content = await generate_stock_report_workbook(start, end)
        check(
            "stock report: no requester (cron) says 'scheduled report'",
            "scheduled report" in str(load_workbook(BytesIO(content))["Current Stock"]["A1"].value),
        )
    except Exception as e:
        check("stock report: new Current Stock columns", False, str(e))

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

            # Orders: sent twice so the second reply must report the first as "last ordered".
            # The item is checked loosely on the first reply since real order history for
            # that item may already exist in the database.
            r = post_webhook(client, "Order:\nDragonfruit 3 kg\nMilk 2 ltr")
            check(
                "Order: block logs every item with a last-ordered line",
                r.status_code == 200 and "Order logged" in r.text and r.text.count("Dragonfruit") == 1 and r.text.count("Milk") == 1
                and r.text.count("ordered") >= 2,
                r.text,
            )
            r = post_webhook(client, "Order: Dragonfruit 4 kg")
            check(
                "second order for the same item reports when it was last ordered",
                r.status_code == 200 and "last ordered" in r.text and "today" in r.text and TEST_SENDER in r.text,
                r.text,
            )
            # Unit conversion + mismatch refusal, on an item unlikely to exist in real data.
            r = post_webhook(client, "In:\nCardamom 2 kg")
            check("stock IN in kg logs a kg balance", r.status_code == 200 and "balance: 2kg" in r.text, r.text)
            r = post_webhook(client, "Out:\nCardamom 500 gms")
            check(
                "stock OUT in gms is converted to kg against the same balance",
                r.status_code == 200 and "-0.5kg" in r.text and "balance: 1.5kg" in r.text and "converted from 500 gms" in r.text,
                r.text,
            )
            r = post_webhook(client, "In:\nCardamom 3 packets")
            check(
                "unconvertible unit mismatch is refused, not mixed into the balance",
                r.status_code == 200 and "Not logged" in r.text and "tracked in kg" in r.text,
                r.text,
            )

            r = post_webhook(client, "In:\nCardamom 3")
            check(
                "stock line with no unit is refused with a request for one (no guessing)",
                r.status_code == 200 and "Not logged" in r.text and "has no unit" in r.text and "kg" in r.text,
                r.text,
            )
            r = post_webhook(client, "Out:\nCardamom 1 kg")
            check("balance is untouched by the refused unitless line", r.status_code == 200 and "balance: 0.5kg" in r.text, r.text)

            r = post_webhook(client, "Order:\nDragonfruit 3")
            check(
                "order line with no unit is refused with a request for one",
                r.status_code == 200 and "has no unit" in r.text and "Order logged" not in r.text,
                r.text,
            )
            r = post_webhook(client, "Order:\nDragonfruit 2 kg\nMilk 4")
            check(
                "mixed order logs the line with a unit and refuses the one without",
                r.status_code == 200 and "Order logged" in r.text and "Dragonfruit" in r.text and "milk 4 has no unit" in r.text,
                r.text,
            )

            r = post_webhook(client, "Order:")
            # The webhook allows 20 requests/minute per IP and this run now sends
            # more than that before the report checks — wait out the window so
            # the later checks aren't rejected by the app's own rate limiter.
            time.sleep(61)
            check(
                "'Order:' with no items falls through (not a stock/order attempt)",
                r.status_code == 200 and "Order logged" not in r.text,
                r.text,
            )

            r = post_webhook(client, "Order Report")
            check(
                "'Order Report' keyword acks the order report immediately",
                r.status_code == 200 and "generating your order report" in r.text,
                r.text,
            )
            r = post_webhook(client, "Get Stock Report")
            check(
                "'Get Stock Report' keyword acks the stock report immediately",
                r.status_code == 200 and "generating your stock report" in r.text,
                r.text,
            )
            # Item plans, sent before the Items Report request below.
            r = post_webhook(client, "Item Plan:\nCorn Cheese Balls 30 pcs\nPaneer Cutlet 35 pcs")
            check(
                "Item Plan: block logs every dish",
                r.status_code == 200 and "Item plan logged" in r.text and "Corn Cheese Balls 30pcs" in r.text and "Paneer Cutlet 35pcs" in r.text,
                r.text,
            )
            r = post_webhook(client, "Item Plan: Paneer Cutlet 20 pcs")
            check("inline 'Item Plan: ...' logs a second plan for the same dish", r.status_code == 200 and "Paneer Cutlet 20pcs" in r.text, r.text)
            r = post_webhook(client, "Item Plan:\nPaneer Papdi 28")
            check(
                "item plan line with no unit is refused with a request for one",
                r.status_code == 200 and "has no unit" in r.text and "Item plan logged" not in r.text,
                r.text,
            )

            r = post_webhook(client, "Items Report")
            check(
                "'Items Report' keyword acks the items report immediately",
                r.status_code == 200 and "generating your items report" in r.text,
                r.text,
            )

            from app.report import generate_item_report_workbook
            from openpyxl import load_workbook
            from io import BytesIO
            wb = load_workbook(BytesIO(asyncio.run(generate_item_report_workbook(requested_by="+919999999999"))))
            ws = wb["Item Plans"]
            data = [[c.value for c in row] for row in ws.iter_rows(min_row=4)]
            cutlet = [row for row in data if row[2] == "paneer cutlet"]
            check(
                "items report: sheets and header line are right",
                wb.sheetnames == ["Item Plans", "Item Summary"] and "requested by +919999999999" in str(ws["A1"].value)
                and [c.value for c in ws[3]] == ["Date", "Planned By", "Item", "Quantity", "Unit"],
                f"{wb.sheetnames} {ws['A1'].value}",
            )
            check(
                "items report: both same-day plans for a dish are kept, attributed to the sender, newest first",
                len(cutlet) >= 2 and all(TEST_SENDER in str(row[1]) for row in cutlet) and cutlet[0][3] == 20 and cutlet[1][3] == 35,
                str(cutlet),
            )
            plan_summary = {row[0].value: [c.value for c in row] for row in wb["Item Summary"].iter_rows(min_row=2)}
            check(
                "items report: summary totals the dish across plans (35 + 20 = 55 over 1 day)",
                plan_summary.get("paneer cutlet", [None] * 6)[2] >= 1 and plan_summary["paneer cutlet"][3] >= 55,
                str(plan_summary.get("paneer cutlet")),
            )

            # Order report contents: the orders logged above must show up, newest first,
            # attributed to the test sender, and be summarised per item.
            from app.report import generate_order_report_workbook
            from openpyxl import load_workbook
            from io import BytesIO
            wb = load_workbook(BytesIO(asyncio.run(generate_order_report_workbook(requested_by="+919999999999"))))
            ws = wb["Orders"]
            data = [[c.value for c in row] for row in ws.iter_rows(min_row=4)]
            dragon = [row for row in data if row[2] == "dragonfruit"]
            dates = [row[0] for row in data]
            check(
                "order report: sheets and header line are right",
                wb.sheetnames == ["Orders", "Item Summary"] and "requested by +919999999999" in str(ws["A1"].value)
                and [c.value for c in ws[3]] == ["Date", "Ordered By", "Item", "Quantity", "Unit"],
                f"{wb.sheetnames} {ws['A1'].value}",
            )
            check(
                "order report: logged orders appear attributed to the sender",
                len(dragon) >= 2 and all(TEST_SENDER in str(row[1]) for row in dragon),
                str(dragon),
            )
            check("order report: rows are newest first", dates == sorted(dates, reverse=True), str(dates[:5]))
            summary = {row[0].value: [c.value for c in row] for row in wb["Item Summary"].iter_rows(min_row=2)}
            check(
                "order report: item summary counts orders and shows who last ordered",
                summary.get("dragonfruit", [None, None, 0])[2] >= 2 and TEST_SENDER in str(summary["dragonfruit"][5]),
                str(summary.get("dragonfruit")),
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
                "wrong-password log request is rejected and flagged as reported",
                r.status_code == 200 and "Incorrect password" in r.text and "reported" in r.text,
                r.text,
            )
            r = post_webhook(client, "send me this month logs")
            check(
                "missing-password log request prompts for the format, not a flat rejection",
                r.status_code == 200 and "Please include the password" in r.text and "Incorrect" not in r.text,
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
        supabase.table("orders").delete().in_("message_id", ids).execute()
        supabase.table("item_plans").delete().in_("message_id", ids).execute()
        supabase.table("messages").delete().in_("id", ids).execute()
    print(f"  removed {len(ids)} test message(s) and their entries/stock rows (sender={TEST_SENDER})")

    # The REPORT / report-request commands' background tasks regenerate this
    # month's report files for real during the live webhook test — remove them.
    month = date.today().strftime("%Y-%m")
    for regenerated in (f"stock_{month}.xlsx", f"ledger_{month}.xlsx", f"messagelog_{month}.xlsx", f"orders_{date.today().isoformat()}.xlsx", f"items_{date.today().isoformat()}.xlsx"):
        try:
            supabase.storage.from_("reports").remove([regenerated])
            print(f"  removed regenerated {regenerated}")
        except Exception:
            pass


def main():
    test_imports()
    test_deterministic_parsing()
    try:
        asyncio.run(test_groq())
        asyncio.run(test_reports())
        test_webhook_live()
    finally:
        # Even if a test crashes partway, never leave synthetic rows behind — they'd
        # skew the next run's balances (a leftover stock row broke the next run's
        # expected values).
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
