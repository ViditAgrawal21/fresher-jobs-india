"""SQL for the public API. Everything is parameterized; no string-built WHERE clauses from user input."""

from __future__ import annotations

# What the site shows: still live, not a copy of another post, tagged, and open to freshers.
VISIBLE = """
    j.is_active
    and j.duplicate_of is null
    and j.tags is not null
    and (j.tags->>'is_fresher')::boolean
"""

SEARCH_JOBS = f"""
with matches as (
    select j.*,
           case when %(q)s::text is null then 0
                else ts_rank(j.search, websearch_to_tsquery('english', %(q)s)) + similarity(j.title, %(q)s)
           end as score
    from jobs j
    where {VISIBLE}
      and (%(q)s::text is null
           or j.search @@ websearch_to_tsquery('english', %(q)s)
           or j.title %% %(q)s                 -- trigram: survives typos like "anlyst"
           or j.company ilike %(q_like)s)
      and (%(roles)s::text[] is null or j.tags->>'role' = any(%(roles)s))
      and (%(cities)s::text[] is null or j.tags->'cities' ?| %(cities)s)
      and (%(types)s::text[] is null or j.tags->>'job_type' = any(%(types)s))
      and (%(days)s::int is null or coalesce(j.posted_at, j.first_seen_at) > now() - make_interval(days => %(days)s))
)
select m.id, m.title, m.company, m.url, m.location_raw, m.source,
       m.tags->>'role' as role,
       m.tags->'cities' as cities,
       m.tags->>'other_city' as other_city,
       m.tags->>'work_mode' as work_mode,
       m.tags->>'job_type' as job_type,
       coalesce(m.posted_at, m.first_seen_at) as posted_at,
       (select coalesce(array_agg(distinct d.source), '{{}}')
          from jobs d where d.duplicate_of = m.id and d.is_active) as also_on,
       count(*) over () as total
from matches m
order by
    case when %(sort)s = 'relevance' then m.score end desc nulls last,
    coalesce(m.posted_at, m.first_seen_at) desc,
    m.id desc
limit %(limit)s offset %(offset)s
"""

FACETS = f"""
select 'role' as facet, j.tags->>'role' as value, count(*) as n from jobs j where {VISIBLE} group by 2
union all
select 'city', c, count(*) from jobs j, jsonb_array_elements_text(j.tags->'cities') c where {VISIBLE} group by 2
union all
select 'job_type', j.tags->>'job_type', count(*) from jobs j where {VISIBLE} group by 2
union all
select 'source', j.source, count(*) from jobs j where {VISIBLE} group by 2
order by facet, n desc
"""

TOTALS = f"""
select
    count(*) filter (where {VISIBLE})                                           as visible,
    count(*) filter (where j.is_active)                                         as active,
    count(*) filter (where j.is_active and j.duplicate_of is not null)          as duplicates,
    count(*) filter (where j.is_active and j.duplicate_of is null and j.tags is not null
                     and not (j.tags->>'is_fresher')::boolean)                  as not_fresher,
    count(*) filter (where j.is_active and j.duplicate_of is null and j.tags is null) as untagged,
    count(*)                                                                    as all_time
from jobs j
"""

RECENT_RUNS = """
select id, started_at, finished_at, status, stats, errors
from runs order by id desc limit %(limit)s
"""

LAST_GOOD_RUN = """
select id, started_at, finished_at, status, stats
from runs where status in ('ok', 'partial') order by id desc limit 1
"""

TAGGING_COST = """
select model,
       sum(n_posts)          as posts,
       sum(input_tokens)     as input_tokens,
       sum(output_tokens)    as output_tokens,
       sum(cost_usd)         as cost_usd,
       count(*)              as calls
from llm_calls
where purpose = 'tagging' and ok
group by model
"""
