import restore


class _Conn:
    def __init__(self): self.closed = False
    def close(self): self.closed = True


def test_main_list_lists_without_db(monkeypatch, capsys):
    called = {"init": False}
    monkeypatch.setattr(restore, "init_db", lambda: called.__setitem__("init", True) or _Conn())
    monkeypatch.setattr(restore.storage_backup, "list_objects",
                        lambda: ["backup_a.zip", "backup_b.zip"])
    restore.main(["--list"])
    out = capsys.readouterr().out
    assert "backup_a.zip" in out and "backup_b.zip" in out
    assert called["init"] is False   # --list must not open a DB connection


def test_main_local_routes_to_restore_database(monkeypatch):
    cap = {}
    monkeypatch.setattr(restore, "init_db", lambda: _Conn())
    monkeypatch.setattr(restore, "restore_database",
                        lambda conn, d, force=False: cap.update(dir=d, force=force) or {"scans": 1})
    restore.main(["--local", "somedir"])
    assert cap == {"dir": "somedir", "force": False}


def test_main_default_routes_to_storage(monkeypatch):
    cap = {}
    monkeypatch.setattr(restore, "init_db", lambda: _Conn())
    monkeypatch.setattr(restore, "restore_from_storage",
                        lambda conn, name, force=False: cap.update(name=name, force=force) or {"scans": 1})
    restore.main(["backup_x.zip", "--force"])
    assert cap == {"name": "backup_x.zip", "force": True}


def test_parse_args_defaults():
    ns = restore._parse_args([])
    assert ns.object_name is None and ns.list is False and ns.local is None and ns.force is False


def test_parse_args_object_and_force():
    ns = restore._parse_args(["backup_x.zip", "--force"])
    assert ns.object_name == "backup_x.zip" and ns.force is True


def test_parse_args_local_and_list():
    ns = restore._parse_args(["--local", "backups", "--list"])
    assert ns.local == "backups" and ns.list is True
