"""Guards against the i18n engine, tables, or language toggle quietly
returning. See sector_momentum-notes/specs/2026-09-13-remove-i18n-support-
design.md — this repo is deliberately English-only; there is no language
toggle and no translation table.
"""
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent


def test_no_i18n_engine_or_tables_in_the_repo():
    assert not (_ROOT / "dashboard/templates/_i18n.html.j2").exists()
    assert not (_ROOT / "dashboard/templates/i18n").exists()


def test_no_i18n_source_references_anywhere():
    """Scoped to dashboard/ and tests/, not the repo root: an unscoped
    rglob() from _ROOT also walks any nested git worktree living under
    .claude/worktrees/ (a sibling Claude Code session on an older branch),
    which can still carry the pre-removal i18n files legitimately --
    flagging them there is a false alarm, not a regression in this repo's
    own tracked source. Caught live: this test passed in the isolated PR
    worktree (which had no nested worktrees of its own) and only failed
    once run from the primary checkout, which did."""
    hits = []
    for root in (_ROOT / "dashboard", _ROOT / "tests"):
        for pattern in ("*.py", "*.j2", "*.js"):
            for path in root.rglob(pattern):
                if path.name in (
                    "plotly.min.js", "supabase.min.js",
                    # This file's own detection logic names the artifacts it
                    # guards against -- excluded so it doesn't flag itself.
                    "test_i18n_removed.py",
                ):
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if "data-i18n" in text or "applyLangToEl" in text or "toggleLang" in text:
                    hits.append(str(path.relative_to(_ROOT)))
    assert not hits, f"i18n artifacts found in source: {hits}"


def test_built_dashboard_has_no_lang_toggle_or_i18n_attributes():
    built = _ROOT / "docs" / "index.html"
    if not built.exists():
        pytest.skip("docs/ not built in this environment")
    html = built.read_text()
    assert 'id="lang-toggle"' not in html
    assert "data-i18n" not in html
    assert "toggleLang" not in html
