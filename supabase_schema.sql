-- Run this in Supabase → SQL Editor

create table messages (
  id                  uuid primary key default gen_random_uuid(),
  sender_phone        text not null,
  message_type        text not null,              -- 'text' | 'image'
  body                text,                       -- message text content
  media_id            text,                       -- whatsapp media id (for images)
  whatsapp_timestamp  bigint,                     -- unix timestamp from whatsapp
  raw_payload         jsonb,                      -- full raw webhook payload
  created_at          timestamptz default now()   -- when our server received it
);

-- Index for fast lookup by sender
create index messages_sender_idx on messages (sender_phone);

-- Index for time-ordered queries
create index messages_created_idx on messages (created_at desc);

-- Optional: enable Row Level Security (good practice)
alter table messages enable row level security;

-- Allow service role full access (your FastAPI backend uses this)
create policy "service role full access"
  on messages
  for all
  using (true);
