# User Authentication (Login Foundation) — Design

**Date:** 2026-07-18
**Status:** Approved
**Backlog item:** User authentication (login) — prerequisite for Position tracking

## Goal

Let Jonas and a small set of invitees sign in to the static GitHub Pages
dashboard, establishing the per-user identity that Position tracking (separate
backlog item) will build on. This item ships the login foundation only: no
user-visible features beyond signing in and out.

## Decisions (from brainstorming)

- **Audience:** Jonas + a few invitees. Invite-only.
- **Login method:** Email magic link (passwordless, `signInWithOtp`).
- **Allowlist enforcement:** Supabase-side — "Allow new users to sign up" is
  disabled in the Supabase Auth settings, and the client calls
  `signInWithOtp({ shouldCreateUser: false })`. Magic links only go to
  accounts that already exist; invitees are added via "Invite user" in the
  Supabase dashboard. No allowlist code or config in the repo.
- **Scope:** Login/logout UI + session handling + RLS hardening of existing
  tables. No new tables (the `positions` table belongs to the follow-up item).

## Architecture

The dashboard stays 100% static. The browser talks directly to Supabase Auth
via `supabase-js` v2; the session lives in `localStorage`. The scan pipeline's
direct-Postgres access (`psycopg2` + `DATABASE_URL`, `postgres` role) is
untouched and unaffected by RLS.

**Flow:** Sign in button (command bar) → inline email form →
`supabase.auth.signInWithOtp({ email, options: { shouldCreateUser: false,
emailRedirectTo: <dashboard URL> } })` → Supabase emails a magic link (only
for existing accounts) → link lands back on the dashboard → supabase-js's
default `detectSessionInUrl` establishes and persists the session →
subsequent visits restore it automatically until expiry or sign-out.

Verified against current Supabase docs (2026-07): the default supabase-js
browser flow handles the magic-link redirect on a plain static page without
email-template customization; magic links are rate-limited to one per 60
seconds per user and expire after 1 hour.

## Components

### `dashboard/assets/auth.js` (new)

Plain JS module, same pattern as `rescore.js`/`scan-digest.js`. Reads the
Supabase URL + publishable key from a `window.SUPABASE_CONFIG` object baked
into the page. Responsibilities:

- Initialize the supabase-js client (only if config present).
- Render auth state into the command-bar control: signed out → "Sign in"
  button + collapsible email form; signed in → user email + "Sign out".
- Send the magic link; surface status ("link sent", "not invited /
  no account", rate-limit "wait a minute", generic errors).
- Listen to `onAuthStateChange` to update the UI without reload.

### supabase-js bundle

Pinned `@supabase/supabase-js` v2 UMD build vendored into
`dashboard/assets/` (same approach as `plotly.min.js` — no CDN at runtime,
no npm toolchain). Loaded only when auth is configured.

### `dashboard/templates/_header.html.j2` (modified)

Auth control in the meta-cluster next to the language toggle, rendered only
when the build has a publishable key. All strings via `data-i18n` (EN+SV).

### Build config (`dashboard/build.py` + templates)

Two values baked at build time into `window.SUPABASE_CONFIG`:

- `SUPABASE_URL` — the project URL (derivation from `DATABASE_URL` already
  exists for Storage; reuse, with `SUPABASE_URL` env override).
- `SUPABASE_PUBLISHABLE_KEY` — new env var / CI secret. Publishable by
  design; data protection comes from RLS and grants, not key secrecy.

If the key is unset, no auth markup, no config object, and no supabase-js
script tag are emitted — the dashboard renders exactly as today. This keeps
local builds and CI (until the secret is added) unchanged.

## Security hardening (same PR)

Enable RLS on all existing tables — `scans`, `scores`, `signals`,
`theme_scores`, `theme_signals`, `sentiment_signals`,
`theme_sentiment_signals` — with **no policies**, blocking the `anon` and
`authenticated` roles entirely. The pipeline connects as `postgres`
(bypasses RLS), so nothing breaks. The project post-dates Supabase's
April 2026 change (new `public` tables are not auto-exposed to the Data
API), so these tables are likely unreachable anyway; RLS is belt-and-braces.
Applied as a SQL step (documented in the PR) and verified with Supabase's
security advisors — target: no advisor warnings.

Never expose `SUPABASE_SERVICE_KEY` or `DATABASE_URL` to the browser; the
only browser-side values are the project URL and the publishable key.

## Supabase-side setup (manual, one-time — post-merge PR steps)

1. Auth settings: disable "Allow new users to sign up".
2. Auth URL configuration: set Site URL to the GitHub Pages URL; add the
   Pages URL and `http://localhost:*` (local testing) to redirect allowlist.
3. Invite users ("Invite user" in the Supabase dashboard): Jonas + invitees.
4. Add `SUPABASE_PUBLISHABLE_KEY` as a GitHub Actions secret and env var for
   the docs build.
5. Run the RLS hardening SQL and check security advisors.

## Error handling

- **Fail-open rendering:** missing config or failed supabase-js load →
  dashboard identical to today, no login control, no console spam.
- **Uninvited email:** `shouldCreateUser: false` returns an error → shown as
  "No account for this email — access is invite-only."
- **Rate limit (1 link / 60 s):** shown as "Please wait a minute and retry."
- **Expired/invalid link:** supabase-js surfaces the error on redirect →
  status line prompts to request a new link.

## Testing

- **Template render tests (pytest):** with a publishable key set, the built
  HTML contains the auth control, `window.SUPABASE_CONFIG`, and the
  supabase-js script tag; with it unset, none of these appear; the service
  key and `DATABASE_URL` never appear in any built HTML in either mode.
- **JS:** signed-out rendering verified via local build + browser preview.
  Full end-to-end magic-link login requires the live Supabase project —
  listed as a post-merge manual verification step in the PR.

## Out of scope

- `positions` table and any per-user data (next backlog item).
- Custom SMTP (built-in mailer is sufficient at this scale).
- OAuth providers, passwords, MFA.
- Gating any dashboard content behind login — everything public stays public.
