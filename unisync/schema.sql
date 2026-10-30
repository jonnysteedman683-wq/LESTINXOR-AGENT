-- UniSync shared ledger for Supabase (Postgres). Mirrors lestinxor/unisync.py.
-- Apply once per project: Supabase SQL editor, or `supabase db push` with this file as a migration.
--
-- Guarantees enforced in the database:
--   * append-only: UPDATE and DELETE on unisync_events are refused by trigger
--   * no forks: seq and prev are unique, so two agents racing on the same head conflict; the loser
--     re-reads the head and retries (optimistic concurrency, no locks held by clients)
--   * no public access: RLS is on with no anon/authenticated policies; agents write with the
--     service-role key held in their own secrets, never in a repo
-- Authority and lifecycle rules are checked by Ledger.from_jsonl / Ledger.post in each agent before
-- it writes, and every reader re-verifies the hash chain on replay.

create table if not exists public.unisync_agents (
    name   text primary key,
    role   text not null check (role in ('owner', 'orchestrator', 'worker', 'watcher')),
    repos  text[] not null default '{}'
);

insert into public.unisync_agents (name, role, repos) values
    ('jonny',           'owner',        '{}'),
    ('lestinxor',       'orchestrator', '{jonnysteedman683-wq/LESTINXOR-AGENT}'),
    ('sebastian',       'worker',       '{jonnysteedman683-wq/Sebastian}'),
    ('jules',           'worker',       '{}'),
    ('claude',          'worker',       '{}'),
    ('hermes',          'worker',       '{}'),
    ('grok',            'watcher',      '{jonnysteedman683-wq/Sebastian}'),
    ('coordinator-bot', 'watcher',      '{}')
on conflict (name) do nothing;

create table if not exists public.unisync_events (
    seq       bigint primary key check (seq >= 0),
    v         text   not null default 'unisync/1',
    sender    text   not null references public.unisync_agents (name),
    kind      text   not null check (kind in (
                  'task.request', 'task.claim', 'task.result', 'task.failed', 'memory.write',
                  'approval.request', 'approval.decision', 'escalate', 'heartbeat')),
    task_id   text   not null default '',
    body      jsonb  not null default '{}'::jsonb,
    prev      char(64) not null unique,
    digest    char(64) not null unique,
    ts        double precision not null,
    inserted  timestamptz not null default now()
);

create index if not exists unisync_events_task_idx on public.unisync_events (task_id) where task_id <> '';
create index if not exists unisync_events_kind_idx on public.unisync_events (kind);

create or replace function public.unisync_refuse_mutation() returns trigger
language plpgsql set search_path = '' as $$
begin
    raise exception 'unisync_events is append-only';
end;
$$;

drop trigger if exists unisync_events_append_only on public.unisync_events;
create trigger unisync_events_append_only
    before update or delete on public.unisync_events
    for each row execute function public.unisync_refuse_mutation();

-- Shared memory: latest value per (scope, key), in ledger order.
create or replace view public.unisync_memory as
select distinct on (coalesce(body ->> 'scope', 'shared'), body ->> 'key')
       coalesce(body ->> 'scope', 'shared') as scope,
       body ->> 'key'                        as key,
       body -> 'value'                       as value,
       sender,
       seq
from public.unisync_events
where kind = 'memory.write'
order by coalesce(body ->> 'scope', 'shared'), body ->> 'key', seq desc;

alter table public.unisync_agents enable row level security;
alter table public.unisync_events enable row level security;
alter view public.unisync_memory set (security_invoker = true);

-- Per-agent tokens for the gateway Edge Function (unisync/gateway/index.ts, deployed as `unisync`).
-- Only sha256(token) is stored. Each token posts only as its agent and only the listed kinds.
-- Revoke: update public.unisync_tokens set revoked_at = now() where agent = '<name>';
create table if not exists public.unisync_tokens (
    id          bigint generated always as identity primary key,
    agent       text not null references public.unisync_agents (name),
    token_hash  char(64) not null unique,
    kinds       text[] not null,
    created_at  timestamptz not null default now(),
    revoked_at  timestamptz
);
alter table public.unisync_tokens enable row level security;
