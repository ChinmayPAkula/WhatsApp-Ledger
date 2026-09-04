"""
Cron entrypoint — generates last month's ledger report and proactively
sends it to the owner on WhatsApp via the Twilio REST API.

Run with: python -m app.monthly_report
"""

import os
import asyncio
from dotenv import load_dotenv
from twilio.rest import Client

from app.database import upload_report
from app.report import generate_report_workbook, resolve_period

load_dotenv()


async def main():
    start, end = resolve_period("last_month")
    print(f"📊 Generating monthly report for {start} to {end}...")
    content = await generate_report_workbook(start, end)
    filename = f"ledger_{start.strftime('%Y-%m')}.xlsx"
    signed_url = await upload_report(filename, content)

    client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    message = client.messages.create(
        from_=os.getenv("TWILIO_WHATSAPP_FROM"),
        to=os.getenv("OWNER_WHATSAPP_TO"),
        body=f"📊 Monthly ledger report for {start.strftime('%B %Y')}",
        media_url=[signed_url],
    )
    print(f"✅ Sent monthly report, SID={message.sid}")


if __name__ == "__main__":
    asyncio.run(main())
