from pathlib import Path

_WF = Path(__file__).parent.parent / ".github" / "workflows" / "scan.yml"


def test_deploys_docs_via_pages_artifact():
    text = _WF.read_text()
    assert "path: docs" in text, "scan workflow must upload the docs/ dir as the Pages artifact"
    assert "actions/deploy-pages" in text, "scan workflow must deploy the Pages artifact"
    assert "git add docs/" not in text, "docs/ is no longer committed — deployed via Pages artifact instead"
    assert "backups/" not in text, "scan workflow must NOT commit the backups/ dir (moved to Storage)"
