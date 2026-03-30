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


def _canonical_request_url_for_hubspot(request: Request) -> str:
    """
    URL HubSpot signs for v2/v3: public scheme + host + path (+ query).
    Behind reverse proxies, use X-Forwarded-* / Host so it matches the Test URL in HubSpot.
    """
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").strip()
    if "," in proto:
        proto = proto.split(",")[0].strip()
    if proto not in ("http", "https"):
        proto = "https"
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").strip()
    if "," in host:
        host = host.split(",")[0].strip()
    if not host:
        return str(request.url)
    path = request.url.path or ""
    qs = request.url.query
    if qs:
        return f"{proto}://{host}{path}?{qs}"
    return f"{proto}://{host}{path}"


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
    method = request.method.upper()
    rx = received.strip().lower()
    for uri in (_canonical_request_url_for_hubspot(request), str(request.url)):
        source = client_secret + method + uri + body_str
        expected = hashlib.sha256(source.encode("utf-8")).hexdigest()
        ex = expected.lower()
        if len(rx) == len(ex) and secrets.compare_digest(ex, rx):
            return True
    return False


def _verify_v1(body: bytes, client_secret: str, received: str) -> bool:
    if not received:
        return False
    rx = received.strip().lower()
    try:
        raw = body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    expected = hashlib.sha256((client_secret + raw).encode("utf-8")).hexdigest()
    ex = expected.lower()
    if len(rx) == len(ex) and secrets.compare_digest(ex, rx):
        return True
    # Alternate: hash raw bytes after secret UTF-8 bytes (some senders)
    ex2 = hashlib.sha256(client_secret.encode("utf-8") + body).hexdigest().lower()
    return len(rx) == len(ex2) and secrets.compare_digest(ex2, rx)


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
    rx = received.strip()
    method = request.method.upper()
    for url in (_canonical_request_url_for_hubspot(request), str(request.url)):
        uri = _decode_uri_for_hubspot_v3(url)
        raw = f"{method}{uri}{body_str}{ts_raw}"
        mac = hmac.new(client_secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).digest()
        expected_b64 = base64.b64encode(mac).decode("ascii")
        if len(rx) == len(expected_b64) and secrets.compare_digest(expected_b64, rx):
            return True
    return False


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
        if _verify_v1(body, secret, sig_main):
            return True
        # v1 is documented for CRM webhooks; if it fails (proxy URL mismatch), try v2 with canonical URL.
        if ver in ("", "v1", "1"):
            return _verify_v2(request, body, secret, sig_main)
        return False
    return False
