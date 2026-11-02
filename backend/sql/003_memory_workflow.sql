alter table public.memories
    add column if not exists confidence numeric(4,3),
    add column if not exists created_by text,
    add column if not exists approved_by text,
    add column if not exists approved_at timestamptz,
    add column if not exists updated_at timestamptz not null default now();

alter table public.memory_sources
    add column if not exists twitch_message_id text,
    add column if not exists source_time timestamptz,
    add column if not exists stream_url text;

create index if not exists memories_search_idx
    on public.memories using gin (to_tsvector('english', title || ' ' || description));
