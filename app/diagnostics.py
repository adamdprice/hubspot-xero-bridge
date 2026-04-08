"""
End-to-end checks: HubSpot token, Xero token/tenant, batch deal list, optional per-deal Xero→HubSpot preview (no PATCH).
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app.config import Settings
from app.deal_sync import deal_xero_sync_read_property_names
from app.hubspot_client import HubSpotClient
from app.services.sync_deal_xero import (
    _xero_invoice_number_is_ignored,
    process_deals_with_xero_invoice_number_sync,
)
from app.xero_client import XeroClient, invoice_fields_for_hubspot
from app.xero_credentials import make_xero_client


def check_hubspot_read(settings: Settings) -> dict[str, Any]:
    """Single CRM read to validate HUBSPOT_ACCESS_TOKEN and deal scope."""
    if not (settings.hubspot_access_token or "").strip():
        return {"ok": False, "step": "hubspot", "error": "HUBSPOT_ACCESS_TOKEN is empty"}
    try:
        hs = HubSpotClient(settings.hubspot_access_token)
        data = hs._request(
            "GET",
            "/crm/v3/objects/deals",
            params={"limit": 1, "properties": "dealname"},
        )
        results = data.get("results") or []
        sample_id = str(results[0]["id"]) if results else None
        return {
            "ok": True,
            "step": "hubspot",
            "sample_deal_id": sample_id,
            "message": "CRM deals read succeeded",
        }
    except Exception as e:
        return {"ok": False, "step": "hubspot", "error": str(e)}


def check_xero_accounting(settings: Settings) -> dict[str, Any]:
    """Minimal Xero Accounting call (Organisation) — validates refresh token + tenant."""
    try:
        xero: XeroClient = make_xero_client(settings)
    except Exception as e:
        return {"ok": False, "step": "xero", "error": str(e)}
    try:
        orgs = xero.get_organisation()
        name = ""
        if orgs and isinstance(orgs[0], dict):
            name = str(orgs[0].get("Name") or orgs[0].get("LegalName") or "").strip()
        return {
            "ok": True,
            "step": "xero",
            "organisation_name": name or None,
            "message": "Accounting API Organisation read succeeded",
        }
    except Exception as e:
        return {"ok": False, "step": "xero", "error": str(e)}


def preview_xero_mapping_for_deal(settings: Settings, deal_id: str) -> dict[str, Any]:
    """
    Load deal from HubSpot, resolve invoice in Xero (same order as sync_deal_from_xero), return mapped status.
    Does not PATCH HubSpot.
    """
    deal_id = (deal_id or "").strip()
    if not deal_id:
        return {"ok": False, "step": "preview_deal", "error": "deal_id is required"}

    hs = HubSpotClient(settings.hubspot_access_token)
    extra = deal_xero_sync_read_property_names(settings)
    if (settings.hubspot_xero_invoice_sync_dealstage_eq or "").strip():
        extra = list(dict.fromkeys(extra + ["dealstage"]))
    try:
        deal = hs.get_deal(deal_id, extra_properties=extra)
    except Exception as e:
        return {"ok": False, "step": "hubspot_get_deal", "deal_id": deal_id, "error": str(e)}

    props = deal.get("properties") or {}
    num_prop = settings.hubspot_deal_prop_xero_invoice_number
    id_prop = settings.hubspot_deal_prop_xero_invoice_id
    st_prop = settings.hubspot_deal_prop_xero_invoice_status
    inv_num_hs = (props.get(num_prop) or "").strip()
    inv_id = (props.get(id_prop) or "").strip()
    hub_status = (props.get(st_prop) or "").strip()

    out: dict[str, Any] = {
        "ok": True,
        "step": "preview_deal",
        "deal_id": deal_id,
        "hubspot": {
            num_prop: inv_num_hs or None,
            id_prop: inv_id or None,
            st_prop: hub_status or None,
            "dealstage": (props.get("dealstage") or "").strip() or None,
        },
    }

    if inv_num_hs and _xero_invoice_number_is_ignored(inv_num_hs, settings):
        out["ok"] = False
        out["error"] = "Invoice number matches ignore list (would skip sync without calling Xero)"
        return out

    try:
        xero = make_xero_client(settings)
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)
        return out

    inv: Optional[dict[str, Any]] = None
    if inv_num_hs:
        inv = xero.get_invoice_by_number(inv_num_hs)
    if inv is None and inv_id:
        try:
            inv = xero.get_invoice(inv_id)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else None
            if code == 404:
                inv = None
            else:
                out["ok"] = False
                out["error"] = f"Xero get invoice by id failed: {e}"
                return out
        except Exception as e:
            out["ok"] = False
            out["error"] = f"Xero get invoice by id failed: {e}"
            return out
    if not inv:
        out["ok"] = False
        out["error"] = (
            "No Xero invoice found for this deal (check invoice number / UUID in HubSpot vs Xero org)."
        )
        return out

    num, mapped_status = invoice_fields_for_hubspot(inv)
    out["xero"] = {
        "InvoiceNumber": inv.get("InvoiceNumber"),
        "InvoiceID": str(inv.get("InvoiceID") or "").strip() or None,
        "Status": inv.get("Status"),
        "AmountDue": inv.get("AmountDue"),
        "AmountPaid": inv.get("AmountPaid"),
        "Total": inv.get("Total"),
        "FullyPaidOnDate": inv.get("FullyPaidOnDate"),
    }
    out["mapped"] = {
        "xero_invoice_number": num,
        "xero_invoice_status": mapped_status,
    }
    out["would_change_status"] = (hub_status or "").lower() != (mapped_status or "").lower()
    return out


def run_pipeline_diagnostics(
    settings: Settings,
    *,
    deal_id: Optional[str] = None,
    max_deals: int = 10,
    include_batch_preview: bool = True,
) -> dict[str, Any]:
    """
    Ordered checks: HubSpot read → Xero Organisation → optional batch deal_ids (dry_run) → optional per-deal Xero preview.
    """
    result: dict[str, Any] = {"summary": []}

    hs_chk = check_hubspot_read(settings)
    result["hubspot"] = hs_chk
    result["summary"].append("hubspot_ok" if hs_chk.get("ok") else "hubspot_failed")

    x_chk = check_xero_accounting(settings)
    result["xero"] = x_chk
    result["summary"].append("xero_ok" if x_chk.get("ok") else "xero_failed")

    if include_batch_preview and hs_chk.get("ok"):
        try:
            batch = process_deals_with_xero_invoice_number_sync(
                settings,
                max_deals=max(1, min(max_deals, 200)),
                dry_run=True,
            )
            result["batch_dry_run"] = {
                "ok": True,
                "queued": batch.get("queued"),
                "deal_ids": batch.get("deal_ids"),
                "search_mode": batch.get("search_mode"),
            }
            result["summary"].append("batch_list_ok")
        except Exception as e:
            result["batch_dry_run"] = {"ok": False, "error": str(e)}
            result["summary"].append("batch_list_failed")
    else:
        result["batch_dry_run"] = {"skipped": True}

    if deal_id and hs_chk.get("ok") and x_chk.get("ok"):
        result["deal_preview"] = preview_xero_mapping_for_deal(settings, deal_id)
        result["summary"].append(
            "deal_preview_ok" if result["deal_preview"].get("ok") else "deal_preview_failed"
        )
    elif deal_id:
        result["deal_preview"] = {
            "skipped": True,
            "reason": "fix hubspot or xero checks first",
        }
    else:
        result["deal_preview"] = {"skipped": True, "hint": "pass deal_id= to see Xero mapping for one deal"}

    return result
