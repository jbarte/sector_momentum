"""README/ARCHITECTURE claims that a config change can silently falsify.

Nothing failed when `README.md` kept advertising three horizon presets for the
week after one was removed — the documentation had no relationship to the code
it described. These are deliberately narrow: they pin only the handful of
statements that name something the config owns, so a preset change breaks a test
rather than leaving a document quietly lying to the next reader.

Not an attempt to verify the prose. Everything else in those files is checked by
reading them.
"""
import re
from pathlib import Path

import pytest

from src.horizons import horizons

_ROOT = Path(__file__).parent.parent
_README = _ROOT / "README.md"
_ARCH = _ROOT / "ARCHITECTURE.md"

# Every preset label the project has ever shipped. A closed literal list on
# purpose: the failure being guarded against is a RETIRED label lingering in the
# docs, and only an explicit vocabulary can distinguish "Short is gone" from
# "Short was never mentioned". Add to this when a new preset is introduced.
_EVER_SHIPPED_LABELS = {"Short", "Medium", "Long"}

_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _real_theme_count() -> int:
    """The actual universe size config/themes.yaml scores — the number this
    test must check the README against, since the README describes the LIVE
    strategy for a real reader, not an arbitrary test fixture."""
    import yaml
    themes_cfg = yaml.safe_load((_ROOT / "config" / "themes.yaml").read_text())
    return len(themes_cfg["themes"])


def _labels():
    return {h.label for h in horizons()}


@pytest.mark.parametrize("path", [_README, _ARCH])
def test_docs_do_not_name_a_retired_horizon_preset(path):
    """The exact drift that prompted these tests: README:31 read
    "A horizon preset (Short / Medium / Long)" for a week after `short` was
    removed, describing a control the reader could not find."""
    retired = _EVER_SHIPPED_LABELS - _labels()
    text = path.read_text()
    for label in sorted(retired):
        # Word-boundary match, so prose like "shorter" or "long-term" is not a hit.
        assert not re.search(rf"\b{label}\b", text), (
            f"{path.name} still names the retired horizon preset {label!r}; "
            f"shipped presets are {sorted(_labels())}"
        )


def test_readme_names_every_shipped_preset():
    text = _README.read_text()
    for label in sorted(_labels()):
        assert re.search(rf"\b{label}\b", text), (
            f"README.md does not mention the {label!r} horizon preset"
        )


def test_readme_states_the_right_number_of_presets():
    """Guards the count independently of the labels: renaming both presets while
    adding a third would pass the test above and still leave "two presets"."""
    n = len(horizons())
    word = _NUMBER_WORDS[n]
    text = _README.read_text()
    assert re.search(rf"\b{word} presets\b", text, re.IGNORECASE), (
        f"README.md should say '{word} presets' — config ships {n}"
    )


def test_readme_quotes_the_shipped_band_edges():
    """The README names concrete sell thresholds ("sell past rank 9"). Those
    are `top_n + buffer_frac`-derived, resolved at today's REAL universe size
    — so a buffer_frac change, OR a theme being added/removed, makes the
    sentence wrong."""
    text = _README.read_text()
    universe = _real_theme_count()
    for h in horizons():
        exit_rank = h.exit_rank(universe)
        assert re.search(rf"\brank {exit_rank}\b", text), (
            f"README.md does not state {h.label}'s exit rank "
            f"({exit_rank} = top_n {h.top_n} + buffer_frac {h.buffer_frac} "
            f"x {universe} themes)"
        )
