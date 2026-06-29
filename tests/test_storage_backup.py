# tests/test_storage_backup.py
import types
import pytest
from src import storage_backup


def test_base_url_derives_from_database_url(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:pw@db.abcdef123.supabase.co:5432/postgres")
    assert storage_backup._base_url() == "https://abcdef123.supabase.co"


def test_base_url_explicit_override(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co/")
    assert storage_backup._base_url() == "https://xyz.supabase.co"


def test_base_url_derives_from_pooler_url(monkeypatch):
    # Supavisor pooler URL: ref is in the username (postgres.<ref>), not the host.
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres.cwhqolfpailtxkiszuvn:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres",
    )
    assert storage_backup._base_url() == "https://cwhqolfpailtxkiszuvn.supabase.co"


class _Resp:
    def __init__(self, content=b"", payload=None):
        self.content = content
        self._payload = payload
    def raise_for_status(self): pass
    def json(self): return self._payload


def test_upload_posts_to_object_url_with_bearer(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc-key")
    calls = {}
    def fake_post(url, data=None, json=None, headers=None, timeout=None):
        calls.update(url=url, data=data, headers=headers)
        return _Resp()
    monkeypatch.setattr(storage_backup.requests, "post", fake_post)
    storage_backup.upload("backup_x.zip", b"ZIPBYTES", bucket="db-backups")
    assert calls["url"] == "https://xyz.supabase.co/storage/v1/object/db-backups/backup_x.zip"
    assert calls["data"] == b"ZIPBYTES"
    assert calls["headers"]["Authorization"] == "Bearer svc-key"


def test_list_objects_returns_sorted_names(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc-key")
    monkeypatch.setattr(storage_backup.requests, "post",
                        lambda *a, **k: _Resp(payload=[{"name": "backup_b.zip"}, {"name": "backup_a.zip"}]))
    assert storage_backup.list_objects() == ["backup_a.zip", "backup_b.zip"]


def test_service_key_required(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    with pytest.raises(RuntimeError):
        storage_backup.download("x.zip")
