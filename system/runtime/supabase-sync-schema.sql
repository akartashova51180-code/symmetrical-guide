-- Prepare only. Review and apply manually to a dedicated/test Supabase project.
-- This migration is intentionally NOT executed by the application.
create table if not exists public.sync_events (
  event_id text not null,
  version integer not null check (version > 0),
  project_id text not null,
  source text not null,
  source_ref text not null,
  created_at timestamptz not null,
  event_type text not null,
  approval text not null,
  privacy text not null check (privacy in ('public_synthetic','internal','restricted')),
  event_text text not null,
  payload jsonb not null,
  payload_sha256 text not null,
  received_at timestamptz not null default now(),
  primary key (event_id, version)
);

create table if not exists public.sync_deliveries (
  event_id text not null,
  version integer not null,
  destination text not null check (destination in ('github','supabase','codex','chatgpt')),
  status text not null default 'pending' check (status in ('pending','delivered','failed')),
  attempts integer not null default 0,
  ack_at timestamptz,
  last_error text not null default '',
  primary key (event_id, version, destination),
  foreign key (event_id, version) references public.sync_events(event_id, version) on delete cascade
);

alter table public.sync_events enable row level security;
alter table public.sync_deliveries enable row level security;
-- No anon/authenticated policies are created. Access must use a private service role
-- from a protected server only; never expose service-role keys to clients.

