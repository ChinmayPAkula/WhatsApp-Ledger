create table if not exists messages (
  id                  uuid primary key default gen_random_uuid(),
  sender_phone        text not null,
  message_type        text not null,              -- 'text' | 'image'
  body                text,                       -- message text content
  media_id            text,                       -- media url (for images)
  whatsapp_timestamp  bigint,                     -- unix timestamp
  raw_payload         jsonb,                      -- full raw webhook payload
  created_at          timestamptz default now()   -- when our server received it
);

create index if not exists messages_sender_idx  on messages (sender_phone);
create index if not exists messages_created_idx  on messages (created_at desc);

alter table messages enable row level security;

drop policy if exists "service role full access" on messages;
create policy "service role full access"
  on messages for all using (true);

create table if not exists entries (
  id              uuid primary key default gen_random_uuid(),
  message_id      uuid references messages(id) on delete cascade,
  entry_type      text,                           -- 'order' | 'delivery' | 'unclear'
  item            text,                           -- "tomato"
  quantity        numeric,                        -- 5
  unit            text,                           -- "kg", "tin", "bunch", "piece"
  category        text,                           -- "Vegetables", "Dairy", etc.
  price_per_unit  numeric,                        -- 40
  total_price     numeric,                        -- 200
  vendor          text,                           -- "raju vegetables", "babu anna"
  notes           text,                           -- AI's free-text observations
  status          text default 'confirmed',       -- 'confirmed' | 'unclear'
  created_at      timestamptz default now()
);

alter table entries add column if not exists vendor text;

create index if not exists entries_item_idx       on entries (item);
create index if not exists entries_category_idx   on entries (category);
create index if not exists entries_type_idx       on entries (entry_type);
create index if not exists entries_message_idx    on entries (message_id);

alter table entries enable row level security;

drop policy if exists "service role full access" on entries;
create policy "service role full access"
  on entries for all using (true);

create table if not exists stock_transactions (
  id          uuid primary key default gen_random_uuid(),
  message_id  uuid references messages(id) on delete cascade,
  direction   text not null,          -- 'in' | 'out'
  item        text not null,
  quantity    numeric not null,
  unit        text,                   -- "bags", "kg", "pieces"
  created_at  timestamptz default now()
);

create index if not exists stock_transactions_item_idx    on stock_transactions (item);
create index if not exists stock_transactions_created_idx on stock_transactions (created_at desc);

alter table stock_transactions enable row level security;

drop policy if exists "service role full access" on stock_transactions;
create policy "service role full access"
  on stock_transactions for all using (true);