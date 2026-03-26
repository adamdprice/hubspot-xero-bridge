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
            token = (get_settings().bridge_auth_token or "").strip()
        except Exception:
            token = (os.getenv("BRIDGE_AUTH_TOKEN") or "").strip()
        if not token:
            return await call_next(request)

        path = request.url.path

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
