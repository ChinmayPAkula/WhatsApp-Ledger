# WhatsApp Store Ledger — Phase 1
### Goal: Receive WhatsApp messages → store in Supabase → read them back

---

## Project structure

```
whatsapp-ledger/
├── app/
│   ├── __init__.py
│   ├── main.py          ← FastAPI app, webhook endpoints
│   ├── models.py        ← Pydantic models for WhatsApp payload
│   └── database.py      ← Supabase read/write functions
├── requirements.txt
├── render.yaml          ← Render deployment config
├── supabase_schema.sql  ← Run this in Supabase SQL editor
├── .env.example         ← Copy to .env and fill in your values
└── .gitignore
```

---

## Step 1 — Meta / WhatsApp setup

### 1a. Create a Meta Developer account
- Go to https://developers.facebook.com
- Sign in with your Facebook account
- Click "My Apps" → "Create App"
- App type: **Business**
- Give it a name (e.g. "Store Ledger")

### 1b. Add WhatsApp to your app
- Inside your app dashboard, click "Add Product"
- Find **WhatsApp** → click "Set Up"
- This gives you access to the WhatsApp Business API

### 1c. Note down these three values (you'll need them for .env)
- **Phone Number ID** — shown in WhatsApp → Getting Started
- **Access Token** — shown on the same page (this is temporary; make it permanent below)
- **WhatsApp Business Account ID** — also on that page

### 1d. Generate a permanent access token
The default token expires. To make it permanent:
- Go to your Facebook app → Settings → Advanced
- Use the Graph API Explorer to generate a `never expire` token
- OR create a System User in Business Manager → assign the app → generate token

### 1e. Pick your VERIFY_TOKEN
This is just a string you make up yourself — any random phrase.
Example: `mystore_secret_webhook_2024`
You'll paste this both in your .env AND in the Meta dashboard when registering the webhook.

---

## Step 2 — Supabase setup

### 2a. Create a project
- Go to https://supabase.com → New project
- Note your **Project URL** and **Service Role Key** (Settings → API)

### 2b. Create the messages table
- Go to Supabase → SQL Editor
- Paste the contents of `supabase_schema.sql`
- Click Run

### 2c. Verify the table exists
- Go to Supabase → Table Editor → you should see the `messages` table

---

## Step 3 — Local setup & testing

```bash
# Clone / navigate to project
cd whatsapp-ledger

# Create virtual environment
python -m venv venv
source venv/bin/activate      # Mac/Linux
# venv\Scripts\activate       # Windows

# Install dependencies
pip install -r requirements.txt

# Copy env file and fill in your values
cp .env.example .env
# Edit .env with your actual tokens

# Run locally
uvicorn app.main:app --reload --port 8000
```

### Test the health check
```
GET http://localhost:8000/
→ {"status": "WhatsApp Ledger backend is running"}
```

### Test reading messages
```
GET http://localhost:8000/messages
→ {"count": 0, "messages": []}
```

---

## Step 4 — Deploy to Render

Meta requires a live HTTPS URL — localhost won't work for webhook registration.

### 4a. Push to GitHub
```bash
git init
git add .
git commit -m "Initial Phase 1 backend"
git remote add origin https://github.com/yourusername/whatsapp-ledger.git
git push -u origin main
```

### 4b. Create Render service
- Go to https://render.com → New → Web Service
- Connect your GitHub repo
- Render auto-detects Python. If not:
  - Build command: `pip install -r requirements.txt`
  - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

### 4c. Add environment variables in Render
In Render → your service → Environment, add:
```
WHATSAPP_TOKEN         = your_permanent_access_token
WHATSAPP_VERIFY_TOKEN  = mystore_secret_webhook_2024
WHATSAPP_PHONE_NUMBER_ID = your_phone_number_id
SUPABASE_URL           = https://xxxx.supabase.co
SUPABASE_SERVICE_KEY   = your_service_role_key
```

### 4d. Note your Render URL
It will look like: `https://whatsapp-ledger.onrender.com`

---

## Step 5 — Register the webhook with Meta

- Go to Meta Developer Dashboard → your app → WhatsApp → Configuration
- Under "Webhook", click **Edit**
- Callback URL: `https://whatsapp-ledger.onrender.com/webhook`
- Verify token: `mystore_secret_webhook_2024` (same as your .env)
- Click **Verify and Save**

Meta will call your GET /webhook — you should see "✅ Webhook verified by Meta" in Render logs.

### Subscribe to message events
- After verifying, under Webhook Fields
- Enable: **messages** ← this is the one that fires when someone sends you a message

---

## Step 6 — Test end to end

### 6a. Send a test message
- In Meta dashboard → WhatsApp → Getting Started
- Use the "Send message" test tool to send a message to your test number
- OR send from your personal WhatsApp to the test number
  (you need to add your number as a test recipient first in the Meta dashboard)

### 6b. Check Render logs
You should see:
```
📩 From: 919876543210 | Type: text | Text: Hello from the store
💾 Saved to Supabase: [{'id': 'uuid...', 'sender_phone': '919876543210', ...}]
```

### 6c. Check Supabase
- Go to Supabase → Table Editor → messages
- Your message should be sitting there as a row

### 6d. Read via API
```
GET https://whatsapp-ledger.onrender.com/messages
→ {"count": 1, "messages": [...]}
```

---

## What each endpoint does

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Health check |
| GET | `/webhook` | Meta verification (called once on setup) |
| POST | `/webhook` | Receives every incoming WhatsApp message |
| GET | `/messages` | Read recent messages from Supabase |

---

## What's stored per message

| Column | Type | What it contains |
|--------|------|-----------------|
| id | uuid | Auto-generated unique ID |
| sender_phone | text | Worker's phone number |
| message_type | text | "text" or "image" |
| body | text | The message text |
| media_id | text | WhatsApp media ID (images only) |
| whatsapp_timestamp | bigint | When the message was sent |
| raw_payload | jsonb | Full raw JSON from Meta |
| created_at | timestamptz | When our server received it |

---

## Phase 1 complete when:
- [ ] Render service is live and GET / returns 200
- [ ] Webhook verified on Meta dashboard
- [ ] You send a WhatsApp message and it appears in Supabase
- [ ] GET /messages returns your message

## Next up — Phase 2
Pass the message body through Groq to extract vendor, items, quantities, dates, category.
