alter table public.communities
    add column if not exists access_token_encrypted text,
    add column if not exists refresh_token_encrypted text,
    add column if not exists token_expires_at timestamptz;
