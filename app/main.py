from __future__ import annotations

import html
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field

from app.auth_bridge import BridgeAuthMiddleware, cookie_https_only, session_secret_key
from app.config import get_settings
from app.deal_sync import deal_xero_search_property_names
from app.hubspot_client import HubSpotClient
from app.services.invoice_from_deal import create_xero_invoice_from_deal
from app.services.manual_invoice import create_manual_draft_invoice
from app.xero_client import XeroClient
from app.xero_oauth import DEFAULT_SCOPES, build_authorize_url, exchange_authorization_code, fetch_connections

load_dotenv()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="HubSpot–Xero bridge", version="0.2.0")

# Session cookie (for BRIDGE_AUTH_TOKEN gate) — must wrap first so later middleware sees request.session
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret_key(),
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=cookie_https_only(),
)
app.add_middleware(BridgeAuthMiddleware)

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
            "xero_oauth_ready": False,
            "error": str(e),
        }
    return {
        "hubspot_configured": bool(s.hubspot_access_token.strip()),
        "hubspot_deal_sync_enabled": s.hubspot_deal_sync_enabled,
        "xero_connected": bool(s.xero_refresh_token.strip() and s.xero_tenant_id.strip()),
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
        return HTMLResponse("Missing code or state.", status_code=400)
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

    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/><title>Xero connected</title></head>
<body style="font-family:system-ui,sans-serif;padding:2rem;max-width:52rem;line-height:1.5">
  <h1>Copy into your <code>.env</code></h1>
  <p>The <strong>refresh token is not shown in the Xero developer portal</strong> — it is only issued here after you authorize.</p>
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


@app.get("/")
def index_page(request: Request):
    """Serve UI; if BRIDGE_AUTH_TOKEN is set, accept ?token= once to set session then redirect."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="UI not found (static/index.html)")
    try:
        s = get_settings()
        t = (s.bridge_auth_token or "").strip()
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
    except HTTPException:
        raise
    except Exception:
        pass
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
        xero = XeroClient(
            settings.xero_client_id,
            settings.xero_client_secret,
            settings.xero_refresh_token,
            settings.xero_tenant_id,
        )
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
        "idempotent": result.idempotent,
    }
