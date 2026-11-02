create extension if not exists pgcrypto;

create table if not exists public.communities (
    id uuid primary key default gen_random_uuid(),
    twitch_broadcaster_id text unique,
    twitch_login text,
    display_name text,
    access_token_encrypted text,
    refresh_token_encrypted text,
    token_expires_at timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists public.memories (
    id uuid primary key default gen_random_uuid(),
    community_id uuid not null references public.communities(id) on delete cascade,
    title text not null,
    description text not null,
    category text not null,
    status text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
    event_time timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists public.memory_sources (
    id uuid primary key default gen_random_uuid(),
    memory_id uuid not null references public.memories(id) on delete cascade,
    source_type text not null,
    excerpt text,
    created_at timestamptz not null default now()
);

create index if not exists communities_twitch_broadcaster_id_idx
    on public.communities (twitch_broadcaster_id);
create index if not exists memories_community_status_idx
    on public.memories (community_id, status);
create index if not exists memory_sources_memory_id_idx
    on public.memory_sources (memory_id);

alter table public.communities enable row level security;
alter table public.memories enable row level security;
alter table public.memory_sources enable row level security;
