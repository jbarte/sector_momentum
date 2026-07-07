import json
import pytest
from src.data import trends_cache
from src.data.trends_cache import cache_object_name, batch_key, DEFAULT_CACHE_BUCKET, load_cache, save_cache


def test_cache_object_name():
    assert cache_object_name("2026-07-07") == "trends_cache_2026-07-07.json"


def test_batch_key_is_order_independent():
    assert batch_key(["XLK", "VGT"]) == batch_key(["VGT", "XLK"]) == "VGT|XLK"


def test_default_cache_bucket():
    assert DEFAULT_CACHE_BUCKET == "trends-cache"


def test_load_cache_parses_downloaded_json(monkeypatch):
    monkeypatch.setattr(trends_cache.storage_backup, "download",
                        lambda name, bucket=None: b'{"US": {"XLK": {"XLK": [1.0]}}}')
    assert load_cache("2026-07-07") == {"US": {"XLK": {"XLK": [1.0]}}}


def test_load_cache_fail_open_on_error(monkeypatch):
    def boom(name, bucket=None):
        raise RuntimeError("404 not found")
    monkeypatch.setattr(trends_cache.storage_backup, "download", boom)
    assert load_cache("2026-07-07") == {}          # missing object -> empty, no raise


def test_load_cache_fail_open_on_bad_json(monkeypatch):
    monkeypatch.setattr(trends_cache.storage_backup, "download",
                        lambda name, bucket=None: b"not json{{{")
    assert load_cache("2026-07-07") == {}


def test_save_cache_uploads_json(monkeypatch):
    captured = {}
    def fake_upload(name, data, bucket=None):
        captured["name"] = name
        captured["data"] = data
    monkeypatch.setattr(trends_cache.storage_backup, "upload", fake_upload)
    save_cache("2026-07-07", {"US": {"XLK": {"XLK": [1.0]}}})
    assert captured["name"] == "trends_cache_2026-07-07.json"
    assert json.loads(captured["data"]) == {"US": {"XLK": {"XLK": [1.0]}}}


def test_save_cache_swallows_upload_error(monkeypatch):
    def boom(name, data, bucket=None):
        raise RuntimeError("network down")
    monkeypatch.setattr(trends_cache.storage_backup, "upload", boom)
    save_cache("2026-07-07", {})    # must not raise
