from pathlib import Path

_WF = Path(__file__).parent.parent / ".github" / "workflows" / "scan.yml"


def test_commit_step_stages_backups():
    text = _WF.read_text()
    assert "git add docs/" in text, "scan workflow must commit the docs/ dir"
    assert "backups/" not in text, "scan workflow must NOT commit the backups/ dir (moved to Storage)"
