"""
Cron entrypoint — generates last month's ledger report and stock report,
proactively sending both to every configured recipient on WhatsApp via the
Twilio REST API.

Run with: python -m app.monthly_report
"""

import os
import asyncio
from dotenv import load_dotenv
from twilio.rest import Client

from app.database import upload_report
from app.report import generate_report_workbook, generate_stock_report_workbook, resolve_period

load_dotenv()


def get_recipients() -> list[str]:
    """OWNER_WHATSAPP_TO is a comma-separated list, e.g.
    'whatsapp:+91...,whatsapp:+91...' — one owner or several."""
    raw = os.getenv("OWNER_WHATSAPP_TO", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


async def main():
    start, end = resolve_period("last_month")
    client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    from_number = os.getenv("TWILIO_WHATSAPP_FROM")
    recipients = get_recipients()

    print(f"📊 Generating monthly ledger report for {start} to {end}...")
    ledger_content = await generate_report_workbook(start, end)
    ledger_url = await upload_report(f"ledger_{start.strftime('%Y-%m')}.xlsx", ledger_content)

    print(f"📦 Generating monthly stock report for {start} to {end}...")
    stock_content = await generate_stock_report_workbook(start, end)
    stock_url = await upload_report(f"stock_{start.strftime('%Y-%m')}.xlsx", stock_content)

    for recipient in recipients:
        ledger_msg = client.messages.create(
            from_=from_number,
            to=recipient,
            body=f"📊 Monthly ledger report for {start.strftime('%B %Y')}",
            media_url=[ledger_url],
        )
        print(f"✅ Sent monthly ledger report to {recipient}, SID={ledger_msg.sid}")

        stock_msg = client.messages.create(
            from_=from_number,
            to=recipient,
            body=f"📦 Monthly stock report for {start.strftime('%B %Y')}",
            media_url=[stock_url],
        )
        print(f"✅ Sent monthly stock report to {recipient}, SID={stock_msg.sid}")


if __name__ == "__main__":
    asyncio.run(main())
