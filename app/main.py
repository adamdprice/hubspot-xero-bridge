from __future__ import annotations

import html
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from pydantic import BaseModel, Field

from app.auth_bridge import BridgeAuthMiddleware, cookie_https_only, session_secret_key
from app.config import Settings, get_settings
from app.deal_sync import deal_xero_search_property_names
from app.hubspot_client import HubSpotClient
from app.services.invoice_from_deal import create_xero_invoice_from_deal
from app.services.manual_invoice import create_manual_draft_invoice
from app.services.sync_deal_xero import process_deals_pending_xero_sync, sync_deal_from_xero
from app.xero_credentials import (
    effective_xero_refresh_token,
    effective_xero_tenant_id,
    make_xero_client,
    xero_refresh_token_source,
)
from app.xero_token_store import get_resolved_sqlite_path, is_token_store_enabled, save_after_oauth
from app.xero_oauth import DEFAULT_SCOPES, build_authorize_url, exchange_authorization_code, fetch_connections

load_dotenv()

_log_webhook = logging.getLogger("hubspot.webhook")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="HubSpot–Xero bridge", version="0.2.0")

# Starlette inserts each add_middleware at index 0, so last-added is outermost on the request path.
# ProxyHeaders first (outermost): trust X-Forwarded-Proto / Host so webhook signatures (v2/v3) match public URL.
# Session must run before BridgeAuth (which reads request.session).
app.add_middleware(BridgeAuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret_key(),
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=cookie_https_only(),
)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Short-lived CSRF state for OAuth (in production, use signed cookies or server-side session store)
_oauth_states: dict[str, float] = {}
_OAUTH_STATE_TTL_SEC = 900.0


def _cleanup_oauth_states() -> None:
    now = time.time()
    expired = [k for k, t in _oauth_states.items() if now - t > _OAUTH_STATE_TTL_SEC]
    for k in expired:
        del _oauth_states[k]


class FromDealBody(BaseModel):
    default_account_code: Optional[str] = None


class ManualInvoiceBody(BaseModel):
    deal_id: str
    unit_amount: float = Field(..., gt=0, description="Line unit amount (typically ex-VAT; Xero applies VAT from TaxType)")
    quantity: float = Field(1.0, gt=0)
    line_description: Optional[str] = None
    xero_contact_id: Optional[str] = None
    create_contact_from_hubspot: bool = False


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/status")
def api_status():
    try:
        s = get_settings()
    except Exception as e:
        return {
            "hubspot_configured": False,
            "xero_connected": False,
            "xero_token_store": False,
            "xero_refresh_token_source": "none",
            "xero_token_sqlite_path": None,
            "xero_oauth_ready": False,
            "error": str(e),
        }
    return {
        "hubspot_configured": bool(s.hubspot_access_token.strip()),
        "hubspot_deal_sync_enabled": s.hubspot_deal_sync_enabled,
        "xero_connected": bool(
            effective_xero_refresh_token(s).strip() and effective_xero_tenant_id(s).strip()
        ),
        "xero_token_store": is_token_store_enabled(),
        "xero_refresh_token_source": xero_refresh_token_source(s),
        "xero_token_sqlite_path": get_resolved_sqlite_path() if is_token_store_enabled() else None,
        "xero_oauth_ready": bool(s.xero_client_id.strip() and s.xero_client_secret.strip()),
        "oauth_start_path": "/auth/xero/start",
        "defaults": {
            "sales_account": s.xero_sales_account_code,
            "item_code": s.xero_item_code,
            "tax_type": s.xero_line_tax_type,
        },
    }


@app.get("/auth/xero/start")
def auth_xero_start():
    """Begin OAuth — the refresh token is returned only after you approve access in the browser."""
    try:
        s = get_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not s.xero_client_id.strip() or not s.xero_client_secret.strip():
        raise HTTPException(
            status_code=400,
            detail="Set XERO_CLIENT_ID and XERO_CLIENT_SECRET in .env first.",
        )
    _cleanup_oauth_states()
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = time.time()
    scopes = (os.getenv("XERO_OAUTH_SCOPES") or "").strip() or DEFAULT_SCOPES
    url = build_authorize_url(s.xero_client_id.strip(), s.xero_redirect_uri.strip(), state, scopes=scopes)
    return RedirectResponse(url)


@app.get("/auth/xero/callback")
def auth_xero_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    if error:
        return HTMLResponse(
            f"<html><body style='font-family:system-ui;padding:2rem'><h1>Xero authorization failed</h1>"
            f"<p>{html.escape(error)}</p><p><a href='/'>Back</a></p></body></html>",
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            "<!DOCTYPE html><html><head><meta charset='utf-8'/><title>Xero OAuth</title></head>"
            "<body style='font-family:system-ui,sans-serif;padding:2rem;max-width:40rem;line-height:1.5'>"
            "<h1>Missing Xero callback parameters</h1>"
            "<p>This page must be reached by <strong>redirect from Xero</strong> after you approve access. "
            "Opening <code>/auth/xero/callback</code> directly, refreshing after login, or using an old bookmark "
            "will not include <code>code</code> and <code>state</code>.</p>"
            "<p><strong>What to do:</strong> start a new connection from "
            "<a href='/auth/xero/start'>/auth/xero/start</a> (or click <strong>Connect Xero</strong> on the app home page), "
            "then sign in and approve — do not bookmark the callback URL.</p>"
            "<p><a href='/'>← Back to app</a></p>"
            "</body></html>",
            status_code=400,
        )
    _cleanup_oauth_states()
    if state not in _oauth_states:
        return HTMLResponse(
            "Invalid or expired state. Open /auth/xero/start again.",
            status_code=400,
        )
    del _oauth_states[state]

    try:
        s = get_settings()
    except Exception as e:
        return HTMLResponse(html.escape(str(e)), status_code=500)

    try:
        tokens = exchange_authorization_code(
            s.xero_client_id.strip(),
            s.xero_client_secret.strip(),
            s.xero_redirect_uri.strip(),
            code,
        )
    except Exception as e:
        return HTMLResponse(
            f"<html><body style='font-family:system-ui;padding:2rem'><h1>Token exchange failed</h1>"
            f"<pre style='white-space:pre-wrap'>{html.escape(str(e))}</pre>"
            f"<p>Ensure XERO_REDIRECT_URI in .env matches the redirect URI in your Xero app exactly "
            f"(e.g. http://localhost:8080/auth/xero/callback).</p></body></html>",
            status_code=400,
        )

    access = tokens.get("access_token") or ""
    refresh = tokens.get("refresh_token") or ""
    if not refresh:
        return HTMLResponse(
            "<html><body style='font-family:system-ui;padding:2rem'>"
            "<h1>No refresh token</h1><p>Ensure the OAuth scopes include <code>offline_access</code>.</p>"
            "</body></html>",
            status_code=400,
        )

    conns = fetch_connections(access)
    rows = []
    for c in conns:
        tid = c.get("tenantId") or c.get("tenant_id")
        tname = c.get("tenantName") or c.get("tenant_name") or ""
        if tid:
            rows.append((str(tid), str(tname)))

    tenants_html = "".join(
        f"<li><strong>{html.escape(name)}</strong><br/><code>{html.escape(tid)}</code></li>"
        for tid, name in rows
    ) or "<li>No tenants returned — check API consent.</li>"

    first_tid: Optional[str] = rows[0][0] if rows else None
    tenant_to_save = None
    if first_tid and not (s.xero_tenant_id or "").strip():
        tenant_to_save = first_tid

    oauth_save_ok = False
    oauth_save_error = ""
    try:
        save_after_oauth(refresh_token=refresh, tenant_id=tenant_to_save)
        oauth_save_ok = True
    except Exception as e:
        oauth_save_error = str(e)

    db_path = html.escape(get_resolved_sqlite_path())
    saved_note = ""
    if is_token_store_enabled():
        if oauth_save_ok:
            saved_note = (
                "<p style='background:#ecfdf5;border:1px solid #6ee7b7;border-radius:8px;padding:1rem'>"
                "<strong>Token saved on disk</strong> at "
                f"<code>{db_path}</code>. "
                "The app will keep using this file when Xero rotates the refresh token — "
                "you do not need to update Railway env vars each time.</p>"
            )
        else:
            saved_note = (
                "<p style='background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:1rem'>"
                "<strong>Could not save the token to disk.</strong> The refresh token below still works until the next deploy, "
                "but you should fix this or paste it into Railway as <code>XERO_REFRESH_TOKEN</code>.<br/><br/>"
                f"<strong>Error:</strong> <code>{html.escape(oauth_save_error)}</code><br/><br/>"
                f"Check volume mount, path <code>{db_path}</code>, and filesystem permissions.</p>"
            )

    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/><title>Xero connected</title></head>
<body style="font-family:system-ui,sans-serif;padding:2rem;max-width:52rem;line-height:1.5">
  <h1>Xero connected</h1>
  {saved_note}
  <p>The <strong>refresh token is not shown in the Xero developer portal</strong> — it is only issued here after you authorize.</p>
  <p style="margin-top:1rem">Local dev: copy into your <code>.env</code> as below. Production with token store: restart is usually unnecessary.</p>
  <label style="display:block;margin-top:1rem;font-weight:600">XERO_REFRESH_TOKEN</label>
  <pre style="background:#f4f4f5;padding:1rem;overflow:auto;border-radius:8px">{html.escape(refresh)}</pre>
  <p style="margin-top:1.5rem;font-weight:600">Pick your organisation UUID for XERO_TENANT_ID:</p>
  <ul>{tenants_html}</ul>
  <p>Add or update:</p>
  <pre style="background:#f4f4f5;padding:1rem;border-radius:8px">XERO_REFRESH_TOKEN={html.escape(refresh)}
XERO_TENANT_ID=&lt;paste one tenant id from above&gt;</pre>
  <p>Restart the bridge, then return to <a href="/">the app</a>.</p>
</body>
</html>"""
    )


def _bridge_auth_token_value() -> str:
    """Token for ?token= gate — must not depend on full Settings() succeeding."""
    try:
        return (get_settings().bridge_auth_token or "").strip()
    except Exception:
        return (os.getenv("BRIDGE_AUTH_TOKEN") or "").strip()


@app.get("/")
def index_page(request: Request):
    """Serve UI; if BRIDGE_AUTH_TOKEN is set, accept ?token= once to set session then redirect."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="UI not found (static/index.html)")
    t = _bridge_auth_token_value()
    if t:
        raw = request.query_params.get("token")
        if raw is not None:
            if secrets.compare_digest(raw.strip(), t):
                request.session["bridge_authenticated"] = True
                from urllib.parse import urlencode

                pairs = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
                target = "/"
                if pairs:
                    target += "?" + urlencode(pairs)
                return RedirectResponse(url=target, status_code=302)
            raise HTTPException(status_code=401, detail="Invalid token")
    return FileResponse(index_path)


@app.get("/api/deals/search")
def search_deals(q: str = Query("", description="Deal name tokens or numeric deal ID")):
    try:
        settings = get_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Configuration error: {e}") from e

    query = (q or "").strip()
    if not query:
        return {"deals": []}

    extra = deal_xero_search_property_names(settings)

    hs = HubSpotClient(settings.hubspot_access_token)
    results: list[dict[str, Any]] = []

    if query.isdigit():
        one = hs.get_deal_safe(query, extra_properties=extra)
        if one:
            results = [one]
        else:
            results = hs.search_deals(query, limit=25, extra_properties=extra)
    else:
        results = hs.search_deals(query, limit=25, extra_properties=extra)

    prop_inv = settings.hubspot_deal_prop_xero_invoice_id
    deals_out = []
    for row in results:
        did = row.get("id")
        if did is None:
            continue
        props = dict(row.get("properties") or {})
        props["xero_invoice_id"] = (props.get(prop_inv) or "").strip()
        deals_out.append({"id": str(did), "properties": props})
    return {"deals": deals_out}


@app.get("/api/deals/{deal_id}/billing")
def deal_billing(deal_id: str):
    try:
        settings = get_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    hs = HubSpotClient(settings.hubspot_access_token)
    extra = deal_xero_search_property_names(settings)
    deal = hs.get_deal_safe(deal_id, extra_properties=extra)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    dprops = deal.get("properties") or {}
    contact_payload = None
    company_payload = None

    cids = hs.get_deal_associated_contact_ids(deal_id)
    if cids:
        c = hs.get_contact(cids[0], properties=["firstname", "lastname", "email", "phone"])
        p = c.get("properties") or {}
        fn = (p.get("firstname") or "").strip()
        ln = (p.get("lastname") or "").strip()
        contact_payload = {
            "id": str(c.get("id")),
            "name": f"{fn} {ln}".strip() or (p.get("email") or "").strip() or "Contact",
            "email": (p.get("email") or "").strip(),
            "phone": (p.get("phone") or "").strip(),
        }

    coids = hs.get_deal_associated_company_ids(deal_id)
    if coids:
        co = hs.get_company(coids[0], properties=["name", "domain", "phone"])
        p = co.get("properties") or {}
        company_payload = {
            "id": str(co.get("id")),
            "name": (p.get("name") or "").strip(),
            "domain": (p.get("domain") or "").strip(),
            "phone": (p.get("phone") or "").strip(),
        }

    return {
        "deal": {
            "id": deal_id,
            "name": (dprops.get("dealname") or "").strip(),
            "amount": dprops.get("amount"),
            "xero_invoice_id": (dprops.get(settings.hubspot_deal_prop_xero_invoice_id) or "").strip(),
            "xero_invoice_number": (dprops.get(settings.hubspot_deal_prop_xero_invoice_number) or "").strip(),
            "xero_invoice_status": (dprops.get(settings.hubspot_deal_prop_xero_invoice_status) or "").strip(),
        },
        "contact": contact_payload,
        "company": company_payload,
    }


@app.get("/api/xero/contacts/search")
def xero_contacts_search(q: str = Query("", min_length=1)):
    try:
        settings = get_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    try:
        xero = make_xero_client(settings)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    try:
        raw = xero.search_contacts(q.strip(), page=1)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xero contact search failed: {e}") from e
    out = []
    for c in raw[:50]:
        cid = c.get("ContactID")
        if not cid:
            continue
        out.append({
            "id": str(cid),
            "name": (c.get("Name") or "").strip(),
            "email": (c.get("EmailAddress") or "").strip(),
        })
    return {"contacts": out}


@app.post("/api/invoices/manual")
def post_manual_invoice(body: ManualInvoiceBody):
    try:
        settings = get_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    result = create_manual_draft_invoice(
        settings,
        body.deal_id.strip(),
        unit_amount=body.unit_amount,
        quantity=body.quantity,
        line_description=body.line_description,
        xero_contact_id=body.xero_contact_id,
        create_contact_from_hubspot=body.create_contact_from_hubspot,
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or "Unknown error")
    return {
        "deal_id": result.deal_id,
        "xero_invoice_id": result.xero_invoice_id,
        "xero_contact_id": result.xero_contact_id,
        "xero_invoice_number": result.xero_invoice_number,
        "xero_invoice_status": result.xero_invoice_status,
    }


@app.post("/api/invoices/from-deal/{deal_id}")
def post_invoice_from_deal(deal_id: str, body: Optional[FromDealBody] = None):
    try:
        settings = get_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Configuration error: {e}") from e

    code = (body.default_account_code if body and body.default_account_code else None) or settings.xero_sales_account_code
    result = create_xero_invoice_from_deal(settings, deal_id, default_account_code=code)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or "Unknown error")
    return {
        "deal_id": result.deal_id,
        "xero_invoice_id": result.xero_invoice_id,
        "xero_contact_id": result.xero_contact_id,
        "xero_invoice_number": result.xero_invoice_number,
        "xero_invoice_status": result.xero_invoice_status,
        "idempotent": result.idempotent,
    }


@app.post("/api/deals/{deal_id}/sync-from-xero")
def post_sync_deal_from_xero(
    deal_id: str,
    force: bool = Query(False, description="If true, sync even when no sync flag/trigger is set."),
):
    """Pull invoice status from Xero into the deal; clears sync_with_xero and xero_sync_trigger when done."""
    try:
        settings = get_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    result = sync_deal_from_xero(settings, deal_id.strip(), require_sync_flag=not force)
    if result.skipped:
        raise HTTPException(
            status_code=400,
            detail=(
                "Set sync_with_xero or xero_sync_trigger (e.g. Sync) on this deal first, "
                "or call with ?force=true."
            ),
        )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or "Sync failed")
    return {"deal_id": result.deal_id, "ok": True}


@app.post("/api/cron/sync-xero")
def post_cron_sync_xero(max_deals: int = Query(50, ge=1, le=100)):
    """Process deals pending sync (sync_with_xero or xero_sync_trigger). Uses same auth as the bridge."""
    try:
        settings = get_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return process_deals_pending_xero_sync(settings, max_deals=max_deals)


def _hubspot_webhook_deal_id(body: dict[str, Any]) -> Optional[str]:
    for key in ("objectId", "dealId", "deal_id"):
        v = body.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _hubspot_subscription_type(body: dict[str, Any]) -> str:
    """HubSpot payloads use subscriptionType; some examples use eventType."""
    return (body.get("subscriptionType") or body.get("eventType") or "").strip()


def _hubspot_webhook_skip(body: dict[str, Any], settings: Settings) -> tuple[bool, str]:
    """
    Skip noisy CRM webhooks (wrong property, cleared dropdown).
    Plain workflow JSON without subscriptionType is never skipped here.
    """
    sub = _hubspot_subscription_type(body)
    if sub != "deal.propertyChange":
        return False, ""
    want_prop = (settings.hubspot_deal_prop_xero_sync_trigger or "").strip()
    pn = (body.get("propertyName") or "").strip()
    if want_prop and pn and pn != want_prop:
        return True, "ignored_property"
    if want_prop and pn == want_prop:
        pv = (body.get("propertyValue") or "").strip()
        want_val = (settings.hubspot_deal_xero_sync_trigger_value or "").strip()
        if want_val and not pv:
            return True, "cleared_trigger"
        if want_val and pv and pv.lower() != want_val.lower():
            return True, "not_sync_selection"
    return False, ""


def _hubspot_webhook_payload_confirms_sync_trigger(body: dict[str, Any], settings: Settings) -> bool:
    """
    True when the webhook proves the user set xero_sync_trigger to the sync value.
    Used to skip require_sync_flag on GET — HubSpot often fires the webhook before the deal read shows the new value.
    """
    if _hubspot_subscription_type(body) != "deal.propertyChange":
        return False
    want_prop = (settings.hubspot_deal_prop_xero_sync_trigger or "").strip()
    pn = (body.get("propertyName") or "").strip()
    if not want_prop or not pn or pn != want_prop:
        return False
    pv = (body.get("propertyValue") or "").strip()
    want_val = (settings.hubspot_deal_xero_sync_trigger_value or "").strip()
    if not want_val:
        return False
    return pv.lower() == want_val.lower()


def _process_hubspot_sync_deal_event(body: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Handle one webhook object (HubSpot sends a JSON array of events per POST)."""
    deal_id = _hubspot_webhook_deal_id(body)
    if not deal_id:
        out: dict[str, Any] = {"ok": False, "error": "missing objectId, dealId, or deal_id"}
        _log_webhook.info("sync-deal %s", json.dumps(out))
        return out

    skip, reason = _hubspot_webhook_skip(body, settings)
    if skip:
        out = {"deal_id": deal_id, "ok": True, "skipped": True, "reason": reason}
        _log_webhook.info("sync-deal %s", json.dumps(out))
        return out

    is_hubspot_crm = bool(_hubspot_subscription_type(body))
    # CRM webhooks require a sync flag on GET unless this event itself proves Sync was set (avoids read-your-writes race).
    payload_confirms_sync = _hubspot_webhook_payload_confirms_sync_trigger(body, settings)
    require_flag = is_hubspot_crm and not payload_confirms_sync

    result = sync_deal_from_xero(settings, deal_id, require_sync_flag=require_flag)
    if result.skipped:
        out = {"deal_id": deal_id, "ok": True, "skipped": True, "reason": "no_sync_pending_on_deal"}
    elif not result.ok:
        out = {"deal_id": deal_id, "ok": False, "error": result.error or "Sync failed"}
    else:
        out = {"deal_id": result.deal_id, "ok": True}
    _log_webhook.info("sync-deal %s", json.dumps(out))
    return out


@app.post("/api/webhooks/hubspot/sync-deal")
async def post_webhook_sync_deal(request: Request):
    """
    Trigger sync for one deal.

    HubSpot CRM webhooks POST a JSON **array** of events; legacy workflows may POST a single object.
    Parse JSON manually so FastAPI does not return 422 for array bodies (Pydantic defaults to object-only).
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON") from None

    try:
        settings = get_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    if isinstance(body, list):
        if not body:
            _log_webhook.info("sync-deal %s", json.dumps({"ok": True, "skipped": True, "reason": "empty_batch"}))
            return {"ok": True, "skipped": True, "reason": "empty_batch"}
        results: list[dict[str, Any]] = []
        for item in body:
            if not isinstance(item, dict):
                results.append({"ok": False, "error": "expected_object_in_batch"})
                _log_webhook.info("sync-deal %s", json.dumps(results[-1]))
                continue
            results.append(_process_hubspot_sync_deal_event(item, settings))
        any_err = any(not r.get("ok") for r in results)
        if any_err:
            # 200 avoids HubSpot retry storms; include per-event errors
            return {"ok": False, "batch": True, "results": results}
        return {"ok": True, "batch": True, "results": results}

    if isinstance(body, dict):
        out = _process_hubspot_sync_deal_event(body, settings)
        if not out.get("ok") and "error" in out:
            raise HTTPException(status_code=400, detail=out.get("error", "Sync failed"))
        return out

    raise HTTPException(status_code=400, detail="Expected JSON object or array")
