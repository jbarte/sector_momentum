-- RLS hardening for browser-facing auth (2026-07-18 auth login foundation).
--
-- Enables RLS with NO policies on every pipeline table: the anon and
-- authenticated Data-API roles are blocked entirely, while the pipeline's
-- direct postgres-role connection (psycopg2 + DATABASE_URL) bypasses RLS
-- and is unaffected. Run once against the production DB (post-merge step),
-- e.g. via the Supabase SQL editor. Idempotent.

alter table public.scans enable row level security;
alter table public.scores enable row level security;
alter table public.signals enable row level security;
alter table public.sentiment_signals enable row level security;
alter table public.theme_scores enable row level security;
alter table public.theme_signals enable row level security;
alter table public.theme_sentiment_signals enable row level security;

-- Verification: every row should show rowsecurity = t
select tablename, rowsecurity
from pg_tables
where schemaname = 'public'
order by tablename;
