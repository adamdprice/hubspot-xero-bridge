"""
HubSpot webhook signature verification (cannot use custom Authorization headers).

See: https://developers.hubspot.com/docs/apps/legacy-apps/authentication/validating-requests
CRM subscription webhooks use v1: SHA-256 hex of (client_secret + raw request body).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request


def _decode_uri_for_hubspot_v3(uri: str) -> str:
    """Decode percent-encoding for characters HubSpot lists for v3 validation."""
    for enc, dec in (
        ("%3A", ":"),
        ("%2F", "/"),
        ("%3F", "?"),
        ("%40", "@"),
        ("%21", "!"),
        ("%24", "$"),
        ("%27", "'"),
        ("%28", "("),
        ("%29", ")"),
        ("%2A", "*"),
        ("%2C", ","),
        ("%3B", ";"),
    ):
        uri = uri.replace(enc, dec)
    return uri


def _verify_v2(request: Request, body: bytes, client_secret: str, received: str) -> bool:
    """v2: SHA-256 hex of client_secret + method + full request URI + body (e.g. workflow webhooks)."""
    if not received:
        return False
    try:
        body_str = body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    uri = str(request.url)
    method = request.method.upper()
    source = client_secret + method + uri + body_str
    expected = hashlib.sha256(source.encode("utf-8")).hexdigest()
    rx = received.strip().lower()
    ex = expected.lower()
    return len(rx) == len(ex) and secrets.compare_digest(ex, rx)


def _verify_v1(body: bytes, client_secret: str, received: str) -> bool:
    if not received:
        return False
    try:
        raw = body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    expected = hashlib.sha256((client_secret + raw).encode("utf-8")).hexdigest()
    rx = received.strip().lower()
    ex = expected.lower()
    return len(rx) == len(ex) and secrets.compare_digest(ex, rx)


def _verify_v3(request: Request, body: bytes, client_secret: str, received: str) -> bool:
    if not received:
        return False
    ts_raw = request.headers.get("x-hubspot-request-timestamp") or ""
    try:
        ts_ms = int(ts_raw)
    except ValueError:
        return False
    if abs(int(time.time() * 1000) - ts_ms) > 300_000:
        return False
    try:
        body_str = body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    uri = _decode_uri_for_hubspot_v3(str(request.url))
    raw = f"{request.method.upper()}{uri}{body_str}{ts_raw}"
    mac = hmac.new(client_secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).digest()
    expected_b64 = base64.b64encode(mac).decode("ascii")
    rx = received.strip()
    return len(rx) == len(expected_b64) and secrets.compare_digest(expected_b64, rx)


def verify_hubspot_webhook_signature(request: Request, body: bytes, client_secret: str) -> bool:
    """
    Return True if the request matches HubSpot's signature using the app's client secret.
    v3: X-HubSpot-Signature-v3 (HMAC). v2/v1: X-HubSpot-Signature (SHA-256 hex); use
    X-HubSpot-Signature-Version to pick v2 vs v1.
    """
    secret = (client_secret or "").strip()
    if not secret:
        return False
    h = request.headers
    sig_v3 = (h.get("x-hubspot-signature-v3") or "").strip()
    sig_main = (h.get("x-hubspot-signature") or "").strip()
    ver = (h.get("x-hubspot-signature-version") or "").strip().lower()

    if sig_v3:
        return _verify_v3(request, body, secret, sig_v3)
    if sig_main:
        if ver == "v2":
            return _verify_v2(request, body, secret, sig_main)
        return _verify_v1(body, secret, sig_main)
    return False
