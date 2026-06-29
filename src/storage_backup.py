"""Thin Supabase Storage REST client (backups bucket) over `requests`.

Credentials: SUPABASE_SERVICE_KEY (service-role). Base URL is SUPABASE_URL if
set, else derived from DATABASE_URL's db.<ref>.supabase.co host.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "db-backups"
_TIMEOUT = 30


def _base_url() -> str:
    explicit = os.environ.get("SUPABASE_URL")
    if explicit:
        return explicit.rstrip("/")
    parsed = urlparse(os.environ.get("DATABASE_URL", ""))
    host = parsed.hostname or ""
    # Direct connection: db.<ref>.supabase.co — ref is in the host.
    if host.startswith("db.") and host.endswith(".supabase.co"):
        ref = host[len("db."):-len(".supabase.co")]
        return f"https://{ref}.supabase.co"
    # Pooler (Supavisor): host is *.pooler.supabase.com and the ref lives in
    # the username as postgres.<ref>.
    if host.endswith(".pooler.supabase.com"):
        user = parsed.username or ""
        ref = user.split(".", 1)[1] if "." in user else ""
        if ref:
            return f"https://{ref}.supabase.co"
    raise RuntimeError(
        "cannot resolve Supabase URL: set SUPABASE_URL, or use a Supabase DATABASE_URL "
        "(direct db.<ref>.supabase.co or a *.pooler.supabase.com pooler URL with user postgres.<ref>)"
    )


def _service_key() -> str:
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_KEY is not set")
    return key


def _headers(extra: dict | None = None) -> dict:
    key = _service_key()
    h = {"Authorization": f"Bearer {key}", "apikey": key}
    if extra:
        h.update(extra)
    return h


def upload(object_name: str, data: bytes, bucket: str = DEFAULT_BUCKET) -> None:
    url = f"{_base_url()}/storage/v1/object/{bucket}/{object_name}"
    resp = requests.post(
        url, data=data,
        headers=_headers({"Content-Type": "application/zip", "x-upsert": "true"}),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()


def download(object_name: str, bucket: str = DEFAULT_BUCKET) -> bytes:
    url = f"{_base_url()}/storage/v1/object/{bucket}/{object_name}"
    resp = requests.get(url, headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.content


def list_objects(bucket: str = DEFAULT_BUCKET) -> list[str]:
    url = f"{_base_url()}/storage/v1/object/list/{bucket}"
    resp = requests.post(
        url,
        json={"prefix": "", "limit": 1000, "sortBy": {"column": "name", "order": "asc"}},
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return sorted(item["name"] for item in resp.json())
