import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import restore


def test_main_passes_dir_and_force(monkeypatch, capsys):
    captured = {}
    class _Conn:
        def close(self): pass
    monkeypatch.setattr(restore, "init_db", lambda: _Conn())
    def fake_restore(conn, backup_dir, *, force):
        captured["dir"] = backup_dir
        captured["force"] = force
        return {"scans": 2, "signals": 4, "scores": 6}
    monkeypatch.setattr(restore, "restore_database", fake_restore)
    monkeypatch.setattr(sys, "argv", ["restore.py", "mybackups", "--force"])
    restore.main()
    assert captured == {"dir": "mybackups", "force": True}
    out = capsys.readouterr().out
    assert "scans" in out and "2" in out


def test_main_defaults(monkeypatch):
    captured = {}
    class _Conn:
        def close(self): pass
    monkeypatch.setattr(restore, "init_db", lambda: _Conn())
    monkeypatch.setattr(restore, "restore_database",
                        lambda conn, backup_dir, *, force: captured.update(dir=backup_dir, force=force) or {})
    monkeypatch.setattr(sys, "argv", ["restore.py"])
    restore.main()
    assert captured == {"dir": "backups", "force": False}
