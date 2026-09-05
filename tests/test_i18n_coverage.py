"""Every key the page asks for in Swedish must exist in the dictionary it reads.

Written 2026-09-03 after a dead-code sweep deleted six *live* keys.
`index.html.j2` renders the Badge scorecard's first column as
`data-i18n="badge_{{ row.badge_key }}"` -- assembled at render time from
`_BADGE_ORDER` -- so grepping the templates for `badge_rising_fast` finds
nothing and the key looks unreferenced. It is not.

Four properties matter; earlier drafts of this file got each of them wrong in
turn, and every mistake had the same shape -- a check that passes while the
page renders English:

1. **Which dictionary.** `_i18n.html.j2` resolves `data-i18n`, `-aria` and
   `-title` from `SV`, but `data-i18n-html` from `SV_HTML`. Merging them hides
   a key defined only in `SV_HTML` yet requested as plain `data-i18n`
   (`note_sentiment` did exactly this: defined in `SV_HTML` for the Sentiment
   page, but also requested via plain `data-i18n` by three tab notes, which
   silently rendered English. Those notes were vestigial -- they described a
   sentiment blend control that had been withdrawn -- and were deleted
   2026-09-05, which is the real reason the mismatch is gone.)
2. **Every emitter.** `data-i18n` is written by templates, by
   `dashboard/*.py` (`breakdown.py`) and by `dashboard/assets/*.js`. Scanning
   templates alone silently ignored `ucits_title`, `unbuyable_note`,
   `trend_tip` and `lastScan`.
3. **Both attribute forms.** A literal `data-i18n="..."` *and*
   `setAttribute("data-i18n", X)`. Only the first was scanned at first, which
   left `BADGE_I18N`'s `badge_entry`/`badge_hold`/`badge_exit` -- reachable
   only via `setAttribute` -- with no guard whatsoever.
4. **Dynamic values must fail loudly.** A value that is not a literal key
   cannot be resolved by scanning, so it must be registered in
   `_DYNAMIC_ATTRS` with the keys it can produce. Skipping what it cannot
   parse is precisely how the badge keys came to look dead.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_I18N_DIR = _ROOT / "dashboard" / "templates" / "i18n"
_TEMPLATES = _ROOT / "dashboard" / "templates"

_ATTR_RE = r"data-i18n(?:-html|-aria|-title)?"

# data-i18n variant -> the dictionary _i18n.html.j2 reads it from.
_ATTR_DICT = {
    "data-i18n": "SV",
    "data-i18n-aria": "SV",
    "data-i18n-title": "SV",
    "data-i18n-html": "SV_HTML",
}

# Values that are not literal keys: assembled by Jinja, built as a JS string,
# or passed as a variable to setAttribute. Each maps to the keys it can
# produce. An unregistered one FAILS the test rather than being skipped.
_DYNAMIC_ATTRS = {
    "badge_{{ row.badge_key }}": None,  # resolved from _BADGE_ORDER below
    # Band-cut rows, built by buildBandCutRowHtml() as a JS string at runtime
    # rather than by Jinja -- so a scan for "{{" misses them too. Keys come
    # from the two insertCutRow() call sites (index.html.j2:1085,1101).
    "' + eyebrowKey + '": {"band_buy_ends", "band_sell_line"},
    "' + p.key + '": {
        "band_buy_note", "band_sell_note_prefix", "band_sell_note_suffix",
    },
    # setAttribute() forms carrying a variable rather than a literal.
    "BADGE_I18N[kind]": {"badge_entry", "badge_hold", "badge_exit"},
    "key": {"position_held_tip", "position_mark_held_tip"},
    # Trend column word (.traj-word): baked via Jinja in the initial render,
    # built as a JS string in rescore.js's trajBadgeInner() for the two
    # client-side repaint paths. Both key off the same trajectory_state.
    "trend_word_{{ row.trajectory_state }}": {
        "trend_word_strong_up", "trend_word_up", "trend_word_flat",
        "trend_word_down", "trend_word_strong_down",
    },
    "trend_word_' + state + '": {
        "trend_word_strong_up", "trend_word_up", "trend_word_flat",
        "trend_word_down", "trend_word_strong_down",
    },
}

# Keys the page requests that do not resolve today. Frozen so the gap cannot
# grow silently; a separate test fails if one starts resolving and is not
# removed from here. A snapshot of known bugs, not an accepted state -- see
# BACKLOG.md's Done entries on untranslated dashboard strings for what has
# lived here before and why.
_KNOWN_BROKEN: set[str] = set()


def _dictionaries() -> dict[str, set[str]]:
    """Keys defined per dictionary, tracking which Object.assign block holds them."""
    out: dict[str, set[str]] = {"SV": set(), "SV_HTML": set()}
    # Read the bundles _i18n.html.j2 actually includes, not whatever sits in
    # the directory: a bundle present but never included would have its keys
    # counted as defined while never loading in the browser.
    included = re.findall(r'{%\s*include\s+"i18n/([\w.]+)"\s*%}',
                          (_TEMPLATES / "_i18n.html.j2").read_text())
    assert included, "no i18n bundles found in _i18n.html.j2"
    for f in [_I18N_DIR / name for name in included]:
        current = None
        for line in f.read_text().splitlines():
            block = re.match(r"\s*Object\.assign\((SV_HTML|SV),", line)
            if block:
                current = block.group(1)
                continue
            # A block ends at a column-0 "});" -- without this, a key-shaped
            # line after it would be attributed to the block just closed and a
            # nonexistent key would look defined.
            if re.match(r"^\}\);", line):
                current = None
                continue
            if current is None:
                continue
            # values are quoted or backtick template literals (guide bodies)
            m = re.match(r"\s*([a-zA-Z][\w]*)\s*:\s*[\"'`]", line)
            if m:
                out[current].add(m.group(1))
    return out


def _badge_keys() -> set[str]:
    from dashboard.badges import _BADGE_ORDER
    return {f"badge_{key}" for _label, key, _held in _BADGE_ORDER}


def _sources() -> list[Path]:
    files = [f for f in sorted(_TEMPLATES.glob("**/*.j2")) if f.parent.name != "i18n"]
    files += sorted((_ROOT / "dashboard").glob("*.py"))
    files += sorted((_ROOT / "dashboard" / "assets").glob("*.js"))
    return [f for f in files if "min.js" not in f.name]


def _requested() -> list[tuple[str, str, str]]:
    """(dictionary, key, source file) for every key anything asks for."""
    out: list[tuple[str, str, str]] = []
    for f in _sources():
        text = f.read_text()
        pairs = re.findall(rf'({_ATTR_RE})="([^"]*)"', text)
        for attr, lit, var in re.findall(
            rf'''setAttribute\(\s*["']({_ATTR_RE})["']\s*,\s*'''
            r'''(?:["']([^"']+)["']|([A-Za-z_$][\w$\[\].]*))\s*\)''', text):
            pairs.append((attr, lit or var))

        for attr, value in pairs:
            target = _ATTR_DICT[attr]
            if re.fullmatch(r"[a-zA-Z][\w]*", value) and value not in _DYNAMIC_ATTRS:
                out.append((target, value, f.name))
            else:
                assert value in _DYNAMIC_ATTRS, (
                    f'{f.name}: {attr}="{value}" is not a literal key and is not '
                    f"registered in _DYNAMIC_ATTRS. Register it with the keys it can "
                    f"produce -- an unregistered dynamic value is invisible to grep, "
                    f"which is how six live badge keys were deleted as dead."
                )
                keys = _DYNAMIC_ATTRS[value] or _badge_keys()
                out.extend((target, k, f.name) for k in keys)
    return out


def test_every_badge_order_key_has_a_swedish_entry():
    """The Badge scorecard's data-i18n is built from _BADGE_ORDER.

    The check whose absence let six live keys be deleted as "dead": the only
    thing tying that dynamic key source to the dictionary.
    """
    sv = _dictionaries()["SV"]
    missing = sorted(_badge_keys() - sv)
    assert not missing, (
        'Badge scorecard renders data-i18n="badge_<key>" for every _BADGE_ORDER '
        f"entry, but these have no Swedish entry: {missing}"
    )


def test_requested_keys_resolve_in_the_dictionary_that_is_read():
    dicts = _dictionaries()
    missing = {
        f"{key} (needs {target})"
        for target, key, _src in _requested()
        if key not in dicts[target] and key not in _KNOWN_BROKEN
    }
    assert not missing, f"keys that will render English in Swedish: {sorted(missing)}"


def test_known_broken_list_has_no_stale_entries():
    """Fixing one of these must also remove it here, or the list rots."""
    dicts = _dictionaries()
    still_broken = {
        key for target, key, _src in _requested()
        if key in _KNOWN_BROKEN and key not in dicts[target]
    }
    assert _KNOWN_BROKEN == still_broken, (
        f"_KNOWN_BROKEN is stale -- now resolving: {sorted(_KNOWN_BROKEN - still_broken)}"
    )
