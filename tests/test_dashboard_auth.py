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
