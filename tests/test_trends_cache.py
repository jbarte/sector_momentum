from src.data.trends_cache import cache_object_name, batch_key, DEFAULT_CACHE_BUCKET


def test_cache_object_name():
    assert cache_object_name("2026-07-07") == "trends_cache_2026-07-07.json"


def test_batch_key_is_order_independent():
    assert batch_key(["XLK", "VGT"]) == batch_key(["VGT", "XLK"]) == "VGT|XLK"


def test_default_cache_bucket():
    assert DEFAULT_CACHE_BUCKET == "trends-cache"
