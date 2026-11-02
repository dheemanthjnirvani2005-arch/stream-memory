create or replace function public.search_approved_memories(
    p_broadcaster_id text,
    p_query text,
    p_limit integer default 5
)
returns table (
    memory_id uuid,
    title text,
    description text,
    category text,
    event_time timestamptz,
    relevance real,
    sources jsonb
)
language sql
stable
security invoker
set search_path = public
as $$
    with selected_community as (
        select id from public.communities where twitch_broadcaster_id = p_broadcaster_id
    ), query_terms as (
        select plainto_tsquery('english', p_query) as tsq
    )
    select m.id, m.title, m.description, m.category, m.event_time,
           ts_rank(to_tsvector('english', m.title || ' ' || m.description), q.tsq) as relevance,
           coalesce(jsonb_agg(jsonb_build_object(
               'excerpt', s.source_excerpt,
               'source_time', s.source_time,
               'source_type', s.source_type
           )) filter (where s.id is not null), '[]'::jsonb) as sources
    from public.memories m
    join selected_community c on c.id = m.community_id
    cross join query_terms q
    left join public.memory_sources s on s.memory_id = m.id
    where m.status = 'approved'
      and to_tsvector('english', m.title || ' ' || m.description) @@ q.tsq
    group by m.id, q.tsq
    order by relevance desc, m.created_at desc
    limit least(greatest(coalesce(p_limit, 5), 1), 10);
$$;
