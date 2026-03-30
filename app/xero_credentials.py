"""Resolve Xero refresh token and tenant: rotated token on disk overrides stale env."""
from __future__ import annotations

import threading
from typing import Any, Optional

from app.config import Settings
from app.xero_token_store import get_stored_refresh_token, get_stored_tenant_id

# One XeroClient per process: each deal used to call make_xero_client() and get a new client with no
# cached access token → OAuth refresh (POST identity.xero.com) before *every* Accounting API call.
# That burst can 429 immediately. Reuse the same client so token refresh is ~once per expiry window.
_client_lock = threading.Lock()
_client_singleton: Optional[Any] = None  # XeroClient
_client_singleton_key: Optional[tuple] = None


def _xero_client_cache_key(settings: Settings) -> tuple:
    return (
        (settings.xero_client_id or "").strip(),
        (settings.xero_client_secret or "").strip(),
        effective_xero_refresh_token(settings),
        effective_xero_tenant_id(settings),
        float(settings.xero_api_min_interval_seconds),
    )


def make_xero_client(settings: Settings):
    from app.xero_client import XeroClient

    global _client_singleton, _client_singleton_key
    k = _xero_client_cache_key(settings)
    with _client_lock:
        if _client_singleton is not None and _client_singleton_key == k:
            return _client_singleton
        c = XeroClient(
            settings.xero_client_id,
            settings.xero_client_secret,
            effective_xero_refresh_token(settings),
            effective_xero_tenant_id(settings),
            min_interval_seconds=settings.xero_api_min_interval_seconds,
        )
        _client_singleton = c
        _client_singleton_key = k
        return c


def effective_xero_refresh_token(settings: Settings) -> str:
    stored = get_stored_refresh_token()
    env = (settings.xero_refresh_token or "").strip()
    return (stored or env).strip()


def xero_refresh_token_source(settings: Settings) -> str:
    """Where the active refresh token came from: disk (preferred), env, or none."""
    stored = get_stored_refresh_token()
    env = (settings.xero_refresh_token or "").strip()
    if (stored or "").strip():
        return "disk"
    if env:
        return "env"
    return "none"


def effective_xero_tenant_id(settings: Settings) -> str:
    # Explicit env tenant wins (ops override); else disk; else env still empty
    env = (settings.xero_tenant_id or "").strip()
    if env:
        return env
    stored = get_stored_tenant_id()
    return (stored or "").strip()
