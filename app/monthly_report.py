"""
Cron entrypoint — generates last month's ledger report and stock report,
proactively sending both to the owner on WhatsApp via the Twilio REST API.

Run with: python -m app.monthly_report
"""

import os
import asyncio
from dotenv import load_dotenv
from twilio.rest import Client

from app.database import upload_report
from app.report import generate_report_workbook, generate_stock_report_workbook, resolve_period

load_dotenv()


async def main():
    start, end = resolve_period("last_month")
    client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    owner = os.getenv("OWNER_WHATSAPP_TO")
    from_number = os.getenv("TWILIO_WHATSAPP_FROM")

    print(f"📊 Generating monthly ledger report for {start} to {end}...")
    ledger_content = await generate_report_workbook(start, end)
    ledger_url = await upload_report(f"ledger_{start.strftime('%Y-%m')}.xlsx", ledger_content)
    ledger_msg = client.messages.create(
        from_=from_number,
        to=owner,
        body=f"📊 Monthly ledger report for {start.strftime('%B %Y')}",
        media_url=[ledger_url],
    )
    print(f"✅ Sent monthly ledger report, SID={ledger_msg.sid}")

    print(f"📦 Generating monthly stock report for {start} to {end}...")
    stock_content = await generate_stock_report_workbook(start, end)
    stock_url = await upload_report(f"stock_{start.strftime('%Y-%m')}.xlsx", stock_content)
    stock_msg = client.messages.create(
        from_=from_number,
        to=owner,
        body=f"📦 Monthly stock report for {start.strftime('%B %Y')}",
        media_url=[stock_url],
    )
    print(f"✅ Sent monthly stock report, SID={stock_msg.sid}")


if __name__ == "__main__":
    asyncio.run(main())
