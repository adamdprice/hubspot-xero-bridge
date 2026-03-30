"""Resolve Xero refresh token and tenant: rotated token on disk overrides stale env."""
from __future__ import annotations

from app.config import Settings
from app.xero_token_store import get_stored_refresh_token, get_stored_tenant_id


def make_xero_client(settings: Settings):
    from app.xero_client import XeroClient

    return XeroClient(
        settings.xero_client_id,
        settings.xero_client_secret,
        effective_xero_refresh_token(settings),
        effective_xero_tenant_id(settings),
    )


def effective_xero_refresh_token(settings: Settings) -> str:
    stored = get_stored_refresh_token()
    env = (settings.xero_refresh_token or "").strip()
    return (stored or env).strip()


def effective_xero_tenant_id(settings: Settings) -> str:
    # Explicit env tenant wins (ops override); else disk; else env still empty
    env = (settings.xero_tenant_id or "").strip()
    if env:
        return env
    stored = get_stored_tenant_id()
    return (stored or "").strip()
