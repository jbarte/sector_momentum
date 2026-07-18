# Auth Login Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Invite-only magic-link login on the static dashboard via Supabase Auth, with RLS hardening — the identity foundation for the later Position-tracking item.

**Architecture:** The dashboard stays 100% static. `dashboard/build.py` bakes a `window.SUPABASE_CONFIG` (project URL + publishable key) into the pages only when `SUPABASE_PUBLISHABLE_KEY` is set; a vendored supabase-js v2 UMD bundle plus a new plain-JS `auth.js` render a sign-in/out control in the command bar. Sign-ups are disabled server-side; RLS (no policies) blocks the `anon`/`authenticated` roles on all existing tables while the pipeline's `postgres`-role connection is untouched.

**Tech Stack:** supabase-js v2 (UMD, vendored like Plotly), Jinja2 templates, plain browser JS (no toolchain), pytest, one SQL hardening script.

**Spec:** `design/specs/2026-07-18-auth-login-foundation-design.md`

## Global Constraints

- **No runtime CDN:** all JS served from `docs/assets/` (supabase-js vendored exactly like `plotly.min.js`: downloaded at build time, gitignored).
- **Fail-open:** with `SUPABASE_PUBLISHABLE_KEY` unset (or the Supabase URL unresolvable, or the bundle download failing), the built dashboard contains **no** auth markup, config, or script tags — identical to today.
- **Secrets:** only the project URL and the publishable key may reach the browser. `SUPABASE_SERVICE_KEY` and `DATABASE_URL` values must never appear in built HTML.
- **i18n:** every user-visible string EN+SV — static markup via `data-i18n` + `dashboard/templates/i18n/_auth.js.j2`; dynamic status messages via a local dict in `auth.js` keyed off `localStorage.getItem("lang")`.
- **No `git add docs/`** on this branch — `docs/` is CI-owned.
- **Conventional commits**, subject < 72 chars.
- **Allowlist is server-side only:** `shouldCreateUser: false` in the client; no allowlist code or emails in the repo.
- **No new DB tables** — the `positions` table belongs to the follow-up backlog item.

---

### Task 1: Build plumbing — auth context, supabase-js bundle, asset copy

**Files:**
- Modify: `dashboard/build.py` (bundle constant near line 98, new functions after `_ensure_plotly_bundle`, wiring in `main()` around lines 291–369)
- Modify: `.gitignore` (after the `dashboard/assets/plotly.min.js` line)
- Modify: `.env.example`
- Test: `tests/test_dashboard_auth.py` (new)

**Interfaces:**
- Produces: `_auth_ctx() -> dict` returning `{"auth": {"url": str, "key": str} | None, "auth_config_json": str}` — merged into all three page contexts; Task 2's templates consume the `auth` and `auth_config_json` context vars.
- Produces: `_ensure_supabase_bundle() -> Path | None` (None = download failed → auth disabled).
- Consumes: `_base_url()` from `src/storage_backup.py` (existing; env `SUPABASE_URL` override, else derived from `DATABASE_URL`; raises `RuntimeError` if neither resolves).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_auth.py`:

```python
"""Tests for the auth login foundation: build-time config + template rendering.

The auth feature is fail-open: without SUPABASE_PUBLISHABLE_KEY the built
dashboard must be identical to today (no markup, no config, no scripts).
Only the project URL and the publishable key may ever reach the browser.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.build import _auth_ctx

_TPL_DIR = Path(__file__).parent.parent / "dashboard" / "templates"


def _jinja_env():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(_TPL_DIR)), keep_trailing_newline=True)
    env.filters["js_json"] = (
        lambda v: v.replace("</", r"<\/") if isinstance(v, str) else v
    )
    return env


def _render_partial(name: str, **ctx) -> str:
    return _jinja_env().get_template(name).render(**ctx)


# ---------------------------------------------------------------------------
# _auth_ctx
# ---------------------------------------------------------------------------

def test_auth_ctx_disabled_without_key(monkeypatch):
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    assert _auth_ctx() == {"auth": None, "auth_config_json": ""}


def test_auth_ctx_enabled_with_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test123")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    ctx = _auth_ctx()
    assert ctx["auth"] == {"url": "https://abc.supabase.co",
                           "key": "sb_publishable_test123"}
    assert json.loads(ctx["auth_config_json"]) == ctx["auth"]


def test_auth_ctx_derives_url_from_database_url(monkeypatch):
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test123")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:pw@db.abc123.supabase.co:5432/postgres",
    )
    assert _auth_ctx()["auth"]["url"] == "https://abc123.supabase.co"


def test_auth_ctx_disabled_when_url_unresolvable(monkeypatch):
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test123")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _auth_ctx() == {"auth": None, "auth_config_json": ""}


def test_auth_ctx_never_contains_secrets(monkeypatch):
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test123")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "SERVICE-ROLE-SECRET")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:DBPASS@db.abc.supabase.co:5432/postgres",
    )
    blob = _auth_ctx()["auth_config_json"]
    assert "SERVICE-ROLE-SECRET" not in blob
    assert "DBPASS" not in blob
```

(The `_render_partial` helper is unused until Task 2 adds template tests to this
same file — that's intentional; it keeps one shared Jinja setup.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_auth.py -v`
Expected: FAIL at import — `cannot import name '_auth_ctx' from 'dashboard.build'`

- [ ] **Step 3: Implement in `dashboard/build.py`**

Resolve the current supabase-js v2 version once, now, and pin it:

Run: `curl -s "https://data.jsdelivr.com/v1/packages/npm/@supabase/supabase-js/resolved?specifier=2"`
Expected: JSON containing `"version": "2.x.y"` — use that exact version below.

Add next to `PLOTLY_CDN` (~line 98):

```python
# Pinned supabase-js v2 UMD build, vendored like Plotly (downloaded once,
# gitignored). Bump deliberately; the dashboard has no JS build toolchain.
SUPABASE_JS_CDN = (
    "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.x.y"
    "/dist/umd/supabase.min.js"
)
```

Add after `_ensure_plotly_bundle()` (~line 124):

```python
def _ensure_supabase_bundle() -> Path | None:
    """Download supabase.min.js once to dashboard/assets/ if not present.

    Fail-open (returns None) unlike the Plotly bundle: a missing auth bundle
    degrades to a dashboard without login, not a broken build.
    """
    _ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    bundle = _ASSETS_DIR / "supabase.min.js"
    if not bundle.exists():
        import requests

        logger.info("Downloading supabase-js bundle from %s …", SUPABASE_JS_CDN)
        try:
            resp = requests.get(SUPABASE_JS_CDN, timeout=30)
            resp.raise_for_status()
            bundle.write_bytes(resp.content)
        except Exception as exc:
            logger.warning(
                "Failed to download supabase-js bundle: %s — auth disabled", exc
            )
            return None
    return bundle


def _auth_ctx() -> dict:
    """Browser auth config: project URL + publishable key, or disabled.

    Only these two values may reach the browser; the publishable key is
    public by design (protection is RLS/grants, not key secrecy).
    """
    import json as _json

    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
    if not key:
        return {"auth": None, "auth_config_json": ""}
    try:
        from src.storage_backup import _base_url
        url = _base_url()
    except Exception as exc:
        logger.warning("Auth disabled: cannot resolve Supabase URL (%s)", exc)
        return {"auth": None, "auth_config_json": ""}
    cfg = {"url": url, "key": key}
    return {"auth": cfg, "auth_config_json": _json.dumps(cfg)}
```

Check `import os` exists at the top of `build.py` (add if missing).
`src.storage_backup` is importable because `main()` already does
`sys.path.insert(0, str(project_root))`; the lazy import inside `_auth_ctx`
keeps module import (used by tests) independent of that.

Wire into `main()`:

1. After `macro_page_ctx = _macro_ctx(shared)` (~line 316):

```python
    auth_ctx = _auth_ctx()
    if auth_ctx["auth"] and _ensure_supabase_bundle() is None:
        auth_ctx = {"auth": None, "auth_config_json": ""}
```

2. Add `sectors_ctx.update(auth_ctx)`, `sentiment_ctx.update(auth_ctx)`,
   `themes_ctx.update(auth_ctx)` right beside the existing
   `.update(macro_page_ctx)` lines (~331/347/364).

3. In the asset-copy block (~lines 291–307), after the `scan-digest.js` copy:

```python
    auth_src = _ASSETS_DIR / "auth.js"
    if auth_src.exists():
        shutil.copy2(auth_src, docs_assets / "auth.js")
    supabase_src = _ASSETS_DIR / "supabase.min.js"
    if supabase_src.exists():
        shutil.copy2(supabase_src, docs_assets / "supabase.min.js")
```

- [ ] **Step 4: gitignore + env example**

`.gitignore` — extend the existing block:

```
# Dashboard assets (large downloaded binary — fetched on first run)
dashboard/assets/plotly.min.js
dashboard/assets/supabase.min.js
```

`.env.example` — append:

```
# Dashboard login (browser-side). Publishable key — safe to expose; data is
# protected by RLS/grants, not key secrecy. Unset = auth UI disabled.
SUPABASE_PUBLISHABLE_KEY=your-publishable-key
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_auth.py -v`
Expected: 5 PASS

Run: `pytest tests/ -v --tb=short -k "dashboard"`
Expected: all PASS (no regressions in existing render tests)

- [ ] **Step 6: Commit**

```bash
git add dashboard/build.py tests/test_dashboard_auth.py .gitignore .env.example
git commit -m "feat: bake Supabase auth config into dashboard build"
```

---

### Task 2: Auth UI — templates, i18n, auth.js

**Files:**
- Create: `dashboard/assets/auth.js`
- Create: `dashboard/templates/i18n/_auth.js.j2`
- Modify: `dashboard/templates/_header.html.j2` (meta-cluster, before the lang-toggle button)
- Modify: `dashboard/templates/_footer.html.j2`
- Modify: `dashboard/templates/_i18n.html.j2` (include list, lines 5–11)
- Modify: `dashboard/templates/css/_chrome.css.j2` (after the `.lang-toggle` rules, ~line 46)
- Test: `tests/test_dashboard_auth.py` (extend)

**Interfaces:**
- Consumes: context vars `auth` (`{"url": str, "key": str} | None`) and `auth_config_json` (str) from Task 1 — available in partials because Jinja `include` inherits context from the page templates.
- Produces: DOM ids `auth-root`, `auth-signin`, `auth-form`, `auth-email`, `auth-send`, `auth-status`, `auth-user`, `auth-email-label`, `auth-signout`; global `window.SUPABASE_CONFIG = {"url": …, "key": …}`.

- [ ] **Step 1: Write the failing render tests**

Append to `tests/test_dashboard_auth.py`:

```python
# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

_AUTH = {"url": "https://abc.supabase.co", "key": "sb_publishable_test123"}
_HEADER_CTX = {
    "active_segment": "sectors",
    "macro": None,
    "active_scan_id": 7,
    "scan_date": "2026-07-18",
}


def test_header_renders_auth_control_when_enabled():
    html = _render_partial("_header.html.j2", auth=_AUTH, **_HEADER_CTX)
    for el_id in ("auth-root", "auth-signin", "auth-form", "auth-email",
                  "auth-status", "auth-user", "auth-signout"):
        assert f'id="{el_id}"' in html


def test_header_omits_auth_control_when_disabled():
    html = _render_partial("_header.html.j2", auth=None, **_HEADER_CTX)
    assert "auth-root" not in html


def test_footer_emits_config_and_scripts_when_enabled():
    html = _render_partial("_footer.html.j2", auth=_AUTH,
                           auth_config_json=json.dumps(_AUTH))
    assert "window.SUPABASE_CONFIG" in html
    assert "assets/supabase.min.js" in html
    assert "assets/auth.js" in html
    assert "sb_publishable_test123" in html


def test_footer_omits_auth_when_disabled():
    html = _render_partial("_footer.html.j2", auth=None, auth_config_json="")
    assert "SUPABASE_CONFIG" not in html
    assert "supabase.min.js" not in html
    assert "auth.js" not in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_auth.py -v`
Expected: the 4 new tests FAIL (`auth-root` not found / `SUPABASE_CONFIG` not found); the 5 Task-1 tests still PASS.

- [ ] **Step 3: Header markup**

In `dashboard/templates/_header.html.j2`, inside `<div class="meta-cluster">`, directly **before** the `<button id="lang-toggle" …>` line:

```jinja
    {% if auth %}
    <span class="auth-root" id="auth-root">
      <button id="auth-signin" class="lang-toggle" type="button" data-i18n="auth_sign_in">Sign in</button>
      <form id="auth-form" class="auth-form" hidden>
        <input id="auth-email" type="email" required autocomplete="email"
               placeholder="you@example.com" aria-label="Email">
        <button id="auth-send" class="lang-toggle" type="submit" data-i18n="auth_send_link">Send link</button>
        <span id="auth-status" class="auth-status" aria-live="polite"></span>
      </form>
      <span id="auth-user" hidden>
        <span id="auth-email-label" class="scan-meta"></span>
        <button id="auth-signout" class="lang-toggle" type="button" data-i18n="auth_sign_out">Sign out</button>
      </span>
    </span>
    {% endif %}
```

- [ ] **Step 4: Footer config + script tags**

In `dashboard/templates/_footer.html.j2`, after the closing `</footer>`:

```jinja
{% if auth %}
<script>window.SUPABASE_CONFIG = {{ auth_config_json | js_json }};</script>
<script src="assets/supabase.min.js"></script>
<script src="assets/auth.js"></script>
{% endif %}
```

(Same `js_json` pattern the pages already use for inline JSON; the footer is
included by all three pages, so this wires every page at once.)

- [ ] **Step 5: i18n**

Create `dashboard/templates/i18n/_auth.js.j2`:

```jinja
Object.assign(SV, {
  auth_sign_in: "Logga in",
  auth_send_link: "Skicka länk",
  auth_sign_out: "Logga ut",
});
```

In `dashboard/templates/_i18n.html.j2`, after the `{% include "i18n/_guides.js.j2" %}` line:

```jinja
  {% include "i18n/_auth.js.j2" %}
```

- [ ] **Step 6: CSS**

In `dashboard/templates/css/_chrome.css.j2`, after the `.lang-toggle:hover` rule (~line 46):

```css
.auth-root { display: inline-flex; align-items: center; gap: 8px; }
.auth-form { display: inline-flex; align-items: center; gap: 6px; }
.auth-form[hidden] { display: none; }
.auth-form input[type="email"] {
  background: transparent;
  color: var(--fg1);
  border: 1px solid var(--fg4);
  border-radius: 4px;
  padding: 2px 6px;
  font: inherit;
  font-size: 0.8rem;
  width: 14em;
}
.auth-status { color: var(--fg4); font-size: 0.75rem; }
```

(If `--fg1` doesn't exist in `_foundation.css.j2`, use the foreground variable
the `.lang-toggle` rule uses.)

- [ ] **Step 7: auth.js**

Create `dashboard/assets/auth.js`:

```js
/* Invite-only magic-link sign-in via Supabase Auth.
 *
 * Loaded only when the build baked window.SUPABASE_CONFIG (publishable key
 * present). Fail-open: any missing piece leaves the dashboard untouched.
 * Sign-ups are disabled server-side; shouldCreateUser:false means links go
 * only to accounts already invited via the Supabase dashboard. */
(function () {
  var cfg = window.SUPABASE_CONFIG;
  var root = document.getElementById("auth-root");
  if (!cfg || !cfg.url || !cfg.key || !root || !window.supabase) return;

  var sb = window.supabase.createClient(cfg.url, cfg.key);

  var signinBtn = document.getElementById("auth-signin");
  var form = document.getElementById("auth-form");
  var emailInput = document.getElementById("auth-email");
  var sendBtn = document.getElementById("auth-send");
  var status = document.getElementById("auth-status");
  var userBox = document.getElementById("auth-user");
  var emailLabel = document.getElementById("auth-email-label");
  var signoutBtn = document.getElementById("auth-signout");

  /* Dynamic strings can't use the data-i18n pass (it runs once per toggle
   * over static nodes), so they carry their own EN/SV pairs. */
  var MSG = {
    sent: {
      en: "Link sent — check your inbox.",
      sv: "Länk skickad — kolla din inkorg.",
    },
    notInvited: {
      en: "No account for this email — access is invite-only.",
      sv: "Inget konto för den här e-postadressen — endast inbjudna.",
    },
    rateLimited: {
      en: "Please wait a minute and try again.",
      sv: "Vänta en minut och försök igen.",
    },
    expired: {
      en: "Link expired — request a new one.",
      sv: "Länken har gått ut — begär en ny.",
    },
    error: {
      en: "Sign-in failed. Try again.",
      sv: "Inloggningen misslyckades. Försök igen.",
    },
  };

  function lang() {
    try { return localStorage.getItem("lang") === "sv" ? "sv" : "en"; }
    catch (e) { return "en"; }
  }

  function setStatus(key) {
    status.textContent = key ? MSG[key][lang()] : "";
  }

  function render(session) {
    var signedIn = !!(session && session.user);
    signinBtn.hidden = signedIn;
    userBox.hidden = !signedIn;
    emailLabel.textContent = signedIn ? (session.user.email || "") : "";
    if (signedIn) {
      form.hidden = true;
      setStatus(null);
    }
  }

  signinBtn.addEventListener("click", function () {
    form.hidden = !form.hidden;
    if (!form.hidden) emailInput.focus();
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    sendBtn.disabled = true;
    setStatus(null);
    sb.auth
      .signInWithOtp({
        email: emailInput.value.trim(),
        options: {
          shouldCreateUser: false,
          emailRedirectTo: window.location.origin + window.location.pathname,
        },
      })
      .then(function (res) {
        if (!res.error) { setStatus("sent"); return; }
        if (res.error.status === 429) setStatus("rateLimited");
        else if (/signup|not allowed|not found/i.test(res.error.message || "")) setStatus("notInvited");
        else setStatus("error");
      })
      .catch(function () { setStatus("error"); })
      .then(function () { sendBtn.disabled = false; });
  });

  signoutBtn.addEventListener("click", function () {
    sb.auth.signOut().catch(function () {});
  });

  /* Fires INITIAL_SESSION on load (restores a persisted session, or picks the
   * magic-link token out of the redirect URL via detectSessionInUrl) and
   * SIGNED_IN / SIGNED_OUT afterwards — the single source of UI state. */
  sb.auth.onAuthStateChange(function (_event, session) {
    render(session);
  });

  /* A failed magic-link redirect (expired/invalid link) comes back with
   * #error=…&error_code=… in the URL instead of a session. */
  var hash = window.location.hash || "";
  if (hash.indexOf("error=") !== -1) {
    form.hidden = false;
    setStatus(hash.indexOf("otp_expired") !== -1 ? "expired" : "error");
  }
})();
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_auth.py -v`
Expected: 9 PASS

Run: `pytest tests/ -v --tb=short`
Expected: all PASS

- [ ] **Step 9: Visual sanity check (fail-open path)**

Run: `python3 dashboard/build.py` **without** `SUPABASE_PUBLISHABLE_KEY` set, then confirm:

Run: `grep -c "auth-root\|SUPABASE_CONFIG" docs/index.html || echo CLEAN`
Expected: `CLEAN` (no auth markup when key unset)

Do **not** commit anything under `docs/` (CI owns it — `git status` must show only source files staged).

- [ ] **Step 10: Commit**

```bash
git add dashboard/assets/auth.js dashboard/templates/i18n/_auth.js.j2 \
  dashboard/templates/_header.html.j2 dashboard/templates/_footer.html.j2 \
  dashboard/templates/_i18n.html.j2 dashboard/templates/css/_chrome.css.j2 \
  tests/test_dashboard_auth.py
git commit -m "feat: add magic-link sign-in UI to dashboard command bar"
```

---

### Task 3: RLS hardening script, CI wiring, backlog

**Files:**
- Create: `scripts/enable_rls.sql`
- Modify: `.github/workflows/build-docs.yml` (step that runs `python3 dashboard/build.py`)
- Modify: `.github/workflows/scan.yml` (step that runs the scan / dashboard rebuild)
- Modify: `BACKLOG.md`

**Interfaces:**
- Consumes: nothing from earlier tasks (independent).
- Produces: nothing consumed later — this is hardening + bookkeeping.

- [ ] **Step 1: Verify the table list against the DDL**

Run: `grep -i "create table" src/state.py`
Expected: exactly these tables: `scans`, `scores`, `signals`, `sentiment_signals`, `theme_scores`, `theme_signals`, `theme_sentiment_signals`. If the DDL has more, add them to the SQL below.

- [ ] **Step 2: Write `scripts/enable_rls.sql`**

```sql
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
```

- [ ] **Step 3: CI env wiring**

In `.github/workflows/build-docs.yml`, on the step that runs `python3 dashboard/build.py`, add (or extend) its `env:` block:

```yaml
        env:
          SUPABASE_PUBLISHABLE_KEY: ${{ secrets.SUPABASE_PUBLISHABLE_KEY }}
```

Do the same in `.github/workflows/scan.yml` on the step that rebuilds the dashboard (search for `dashboard/build.py`; if the scan step runs it implicitly via `scan.py`, add the env var to that step). Keep existing env entries — append, don't replace. An absent secret resolves to an empty string, which `_auth_ctx` treats as disabled, so this is safe to merge before the secret exists.

- [ ] **Step 4: BACKLOG.md**

First sync with main (PR #103 adds the auth Queued section and may or may not be merged yet):

```bash
git fetch origin main && git merge origin/main
```

- If the Queued section **"## User authentication (login)"** exists: delete the whole section (backlog rule: shipping deletes Queued in the same PR).
- If PR #103 is still unmerged (section absent): skip the deletion — but note in the PR body that the Queued section must be removed when both PRs are in (or re-merge main before opening the PR once #103 lands).
- Leave the **"## Position tracking"** Queued section untouched (its "depends on User authentication being shipped first" text stays accurate).

Add at the **top of Done**:

```markdown
- **User authentication (login foundation)** — invite-only magic-link sign-in
  on the static dashboard via Supabase Auth + supabase-js v2 (UMD bundle
  vendored at build time like Plotly, gitignored). Sign in/out control in the
  command-bar meta-cluster (EN+SV); session persisted in localStorage;
  `dashboard/assets/auth.js` + `window.SUPABASE_CONFIG` baked by `build.py`
  only when `SUPABASE_PUBLISHABLE_KEY` is set — fail-open, without the key
  the dashboard is unchanged. Allowlist is server-side: Supabase sign-ups
  disabled + `shouldCreateUser: false`; invitees added via the Supabase
  dashboard. RLS enabled (no policies) on all 7 pipeline tables
  (`scripts/enable_rls.sql`) — anon/authenticated blocked, postgres-role
  pipeline unaffected. Foundation for Position tracking (queued). *(2026-07-18)*
```

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v --tb=short`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/enable_rls.sql .github/workflows/build-docs.yml \
  .github/workflows/scan.yml BACKLOG.md
git commit -m "feat: add RLS hardening script and auth CI wiring"
```

---

## Post-merge manual steps (goes in the PR body)

1. Supabase dashboard → Auth → Sign In / Up: **disable "Allow new users to sign up"**.
2. Auth → URL Configuration: Site URL = `https://jbarte.github.io/sector_momentum/`; add that URL and `http://localhost:*` to the redirect allowlist.
3. Auth → Users → **Invite user**: Jonas + invitees.
4. Project Settings → API keys: copy the **publishable** key → add as GitHub Actions secret `SUPABASE_PUBLISHABLE_KEY` (and to local `.env`).
5. SQL editor: run `scripts/enable_rls.sql`; confirm every table shows `rowsecurity = t`; check Security Advisors for warnings.
6. Trigger `build-docs.yml` (or wait for the next scan), then verify on the live site: sign-in control visible; magic-link round-trip works; an uninvited email gets the invite-only message.
