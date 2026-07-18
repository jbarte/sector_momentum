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
