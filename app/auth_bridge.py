"""
Optional gate for publicly deployed bridges (Railway, etc.).

Set BRIDGE_AUTH_TOKEN to a long random secret. Then:
- Open the app with ?token=THAT_SECRET (e.g. from HubSpot link) — sets a session cookie and redirects.
- Or send Authorization: Bearer THAT_SECRET on API calls.

If BRIDGE_AUTH_TOKEN is unset/empty, auth is disabled (local dev).
"""
from __future__ import annotations

import hashlib
import os
import secrets
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings
from app.hubspot_webhook_verify import verify_hubspot_webhook_signature

HUBSPOT_SYNC_WEBHOOK_PATH = "/api/webhooks/hubspot/sync-deal"


def session_secret_key() -> str:
    """Starlette SessionMiddleware needs a stable secret; prefer BRIDGE_SESSION_SECRET, else derive from BRIDGE_AUTH_TOKEN."""
    try:
        s = get_settings()
        raw = (s.bridge_session_secret or "").strip()
        if len(raw) >= 32:
            return raw
        t = (s.bridge_auth_token or "").strip()
        if t:
            return hashlib.sha256(t.encode("utf-8")).hexdigest()
    except Exception:
        pass
    raw = (os.getenv("BRIDGE_SESSION_SECRET") or "").strip()
    if len(raw) >= 32:
        return raw
    t = (os.getenv("BRIDGE_AUTH_TOKEN") or "").strip()
    if t:
        return hashlib.sha256(t.encode("utf-8")).hexdigest()
    return "dev-only-insecure-do-not-use-in-production"


def cookie_https_only() -> bool:
    try:
        return bool(get_settings().bridge_cookie_secure)
    except Exception:
        return (os.getenv("BRIDGE_COOKIE_SECURE") or "").strip().lower() in ("1", "true", "yes")


class BridgeAuthMiddleware(BaseHTTPMiddleware):
    """Require BRIDGE_AUTH_TOKEN via session cookie or Bearer header when configured."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            settings = get_settings()
            token = (settings.bridge_auth_token or "").strip()
            hubspot_secret = (settings.hubspot_client_secret or "").strip()
        except Exception:
            token = (os.getenv("BRIDGE_AUTH_TOKEN") or "").strip()
            hubspot_secret = (os.getenv("HUBSPOT_CLIENT_SECRET") or "").strip()

        path = request.url.path

        # HubSpot webhooks cannot send Authorization; they send X-HubSpot-Signature (client secret + body).
        if path == HUBSPOT_SYNC_WEBHOOK_PATH and request.method == "POST" and (token or hubspot_secret):
            body = await request.body()

            def _replay_receive(b: bytes):
                async def receive():
                    return {"type": "http.request", "body": b, "more_body": False}

                return receive

            hs_ok = bool(hubspot_secret and verify_hubspot_webhook_signature(request, body, hubspot_secret))
            bearer_ok = False
            if token:
                auth_h = request.headers.get("authorization") or ""
                if auth_h.lower().startswith("bearer "):
                    got = auth_h[7:].strip()
                    bearer_ok = secrets.compare_digest(got, token)

            if hs_ok or bearer_ok:
                return await call_next(Request(request.scope, _replay_receive(body)))

            return JSONResponse(
                {
                    "detail": (
                        "Unauthorized. For HubSpot webhooks set HUBSPOT_CLIENT_SECRET to your app client secret "
                        "(validates X-HubSpot-Signature). Or send Authorization: Bearer with BRIDGE_AUTH_TOKEN."
                    )
                },
                status_code=401,
            )

        if not token:
            return await call_next(request)

        if path == "/health":
            return await call_next(request)
        if path.startswith("/auth/xero"):
            return await call_next(request)

        auth_header = request.headers.get("authorization") or ""
        if auth_header.lower().startswith("bearer "):
            got = auth_header[7:].strip()
            if secrets.compare_digest(got, token):
                return await call_next(request)

        if request.session.get("bridge_authenticated") is True:
            return await call_next(request)

        # Let GET / validate ?token= and set session (see index route)
        if path == "/" and request.method == "GET" and request.query_params.get("token") is not None:
            return await call_next(request)

        if path.startswith("/api"):
            return JSONResponse(
                {
                    "detail": (
                        "Unauthorized. Set BRIDGE_AUTH_TOKEN in production and open the app with "
                        "?token=… or use Authorization: Bearer."
                    )
                },
                status_code=401,
            )

        if path == "/" and request.method == "GET":
            return Response(
                content=(
                    "<!DOCTYPE html><html><head><meta charset='utf-8'/><title>Sign in required</title></head>"
                    "<body style='font-family:system-ui,sans-serif;padding:2rem;max-width:36rem;line-height:1.5'>"
                    "<h1>HubSpot–Xero bridge</h1>"
                    "<p>This server requires a shared secret. Use the link from your HubSpot deal "
                    "(it includes <code>?token=…</code>), or add <code>?token=YOUR_SECRET</code> once in this browser.</p>"
                    "<p style='color:#64748b;font-size:0.9rem'>Configure <code>BRIDGE_AUTH_TOKEN</code> on the host "
                    "(e.g. Railway variables).</p>"
                    "</body></html>"
                ),
                status_code=401,
                media_type="text/html; charset=utf-8",
            )

        return await call_next(request)
