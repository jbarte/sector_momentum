import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dashboard.build import _disable_jekyll


def test_disable_jekyll_creates_empty_nojekyll(tmp_path):
    out = tmp_path / "docs"
    created = _disable_jekyll(out)
    assert created == out / ".nojekyll"
    assert created.is_file()
    assert created.read_text() == ""


def test_disable_jekyll_creates_missing_out_dir(tmp_path):
    # build.py may run before the output dir exists.
    out = tmp_path / "nested" / "docs"
    _disable_jekyll(out)
    assert (out / ".nojekyll").is_file()
