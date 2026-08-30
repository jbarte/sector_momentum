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


# --- delete ------------------------------------------------------------

def test_delete_sends_bucket_scoped_delete_with_prefixes_body(monkeypatch):
    """Supabase Storage's bulk-delete contract (storage-js `remove()`):
    DELETE {base}/storage/v1/object/{bucket} with a JSON body of
    {"prefixes": [exact object names]} — "prefixes" is the API's parameter
    name, but each entry must be a full, exact object path, not a
    directory-style prefix that could match more than intended."""
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc-key")
    calls = {}
    def fake_delete(url, json=None, headers=None, timeout=None):
        calls.update(url=url, json=json, headers=headers)
        return _Resp()
    monkeypatch.setattr(storage_backup.requests, "delete", fake_delete)
    storage_backup.delete(["backup_a.zip", "backup_b.zip"], bucket="db-backups")
    assert calls["url"] == "https://xyz.supabase.co/storage/v1/object/db-backups"
    assert calls["json"] == {"prefixes": ["backup_a.zip", "backup_b.zip"]}
    assert calls["headers"]["Authorization"] == "Bearer svc-key"


def test_delete_of_an_empty_list_makes_no_request(monkeypatch):
    """Defensive: never call the delete endpoint with nothing to delete — an
    empty selector should never be relied on to mean 'nothing', belt and
    suspenders against a bulk-delete footgun."""
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc-key")
    def fail(*a, **k):
        raise AssertionError("requests.delete must not be called for an empty list")
    monkeypatch.setattr(storage_backup.requests, "delete", fail)
    storage_backup.delete([], bucket="db-backups")  # must not raise
