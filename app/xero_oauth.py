"""
Xero OAuth2 authorization-code flow: refresh token only appears after browser consent.
"""
from __future__ import annotations

from urllib.parse import urlencode

import httpx

AUTH_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"

# offline_access → refresh_token. OpenID → user id.
# From 2026-03-02, NEW Xero apps must use granular scopes (broad accounting.transactions is rejected).
# See https://developer.xero.com/documentation/guides/oauth2/scopes/
DEFAULT_SCOPES = (
    "openid profile email offline_access "
    "accounting.invoices accounting.contacts accounting.settings"
)


def build_authorize_url(client_id: str, redirect_uri: str, state: str, scopes: str = DEFAULT_SCOPES) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_authorization_code(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
) -> dict:
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        r.raise_for_status()
        return r.json()


def fetch_connections(access_token: str) -> list[dict]:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            CONNECTIONS_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else []
