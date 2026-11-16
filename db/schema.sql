-- Idempotent: safe to run on every deploy (python -m pipeline.migrate).
create extension if not exists pg_trgm;

create table if not exists runs (
    id           bigserial primary key,
    started_at   timestamptz not null default now(),
    finished_at  timestamptz,
    status       text not null default 'running',   -- running | ok | partial | failed
    stats        jsonb not null default '{}'::jsonb,
    errors       jsonb not null default '[]'::jsonb
);

create table if not exists jobs (
    id                  bigserial primary key,
    source              text not null,               -- adzuna | jooble | greenhouse | lever | ashby | smartrecruiters
    source_id           text not null,
    url                 text not null,
    canonical_url       text not null,
    title               text not null,
    company             text not null,
    company_norm        text not null,
    location_raw        text not null default '',
    description         text not null default '',
    posted_at           timestamptz,
    first_seen_at       timestamptz not null default now(),
    last_seen_at        timestamptz not null default now(),
    last_seen_run       bigint references runs(id),
    is_active           boolean not null default true,
    content_hash        text not null,
    dedup_key           text not null,
    duplicate_of        bigint references jobs(id) on delete set null,
    -- LLM output (see core/models.py JobTags). JSONB so new tags need no migration.
    tags                jsonb,
    tag_model           text,
    tag_prompt_version  text,
    tagged_at           timestamptz,
    search              tsvector generated always as (
                            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                            setweight(to_tsvector('english', coalesce(company, '')), 'B') ||
                            setweight(to_tsvector('english', coalesce(left(description, 2000), '')), 'C')
                        ) stored,
    unique (source, source_id)
);

-- What the website actually queries: active, not a duplicate, tagged.
create index if not exists jobs_visible_idx on jobs (posted_at desc) where is_active and duplicate_of is null and tags is not null;
create index if not exists jobs_search_idx on jobs using gin (search);
create index if not exists jobs_title_trgm_idx on jobs using gin (title gin_trgm_ops);
create index if not exists jobs_tags_idx on jobs using gin (tags jsonb_path_ops);
create index if not exists jobs_dedup_key_idx on jobs (dedup_key);
create index if not exists jobs_canonical_url_idx on jobs (canonical_url);
create index if not exists jobs_untagged_idx on jobs (id) where tags is null and is_active;

-- One row per LLM request, so cost per 1,000 posts comes from real token counts.
create table if not exists llm_calls (
    id                  bigserial primary key,
    run_id              bigint references runs(id),
    created_at          timestamptz not null default now(),
    model               text not null,
    prompt_version      text not null,
    purpose             text not null default 'tagging',  -- tagging | eval
    n_posts             int not null,
    input_tokens        int not null default 0,
    output_tokens       int not null default 0,
    cache_read_tokens   int not null default 0,
    cache_write_tokens  int not null default 0,
    cost_usd            numeric(12, 6) not null default 0,
    latency_ms          int,
    ok                  boolean not null default true,
    error               text
);
