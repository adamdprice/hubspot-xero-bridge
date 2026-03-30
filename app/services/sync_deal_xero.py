"""
Pull invoice status (and number / Xero ID) from Xero into HubSpot when sync_with_xero is set.

Uses xero_invoice_id (preferred) or invoice_number to find the invoice in Xero.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Optional

from app.config import Settings
from app.deal_sync import deal_xero_sync_read_property_names, patch_deal_xero
from app.hubspot_client import HubSpotClient
from app.xero_credentials import make_xero_client
from app.xero_client import invoice_fields_for_hubspot


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _utc_today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _hs_bool_true(val: Any) -> bool:
    if val is True:
        return True
    s = str(val).strip().lower()
    return s in ("true", "1", "yes")


@dataclass
class SyncDealXeroResult:
    ok: bool
    deal_id: str
    skipped: bool = False
    error: Optional[str] = None


def _patch_sync_error(hs: HubSpotClient, settings: Settings, deal_id: str, message: str) -> None:
    patch_deal_xero(
        hs,
        settings,
        deal_id,
        {
            settings.hubspot_deal_prop_xero_last_error: message[:500],
            settings.hubspot_deal_prop_xero_sync_last_error_date: _utc_today(),
            settings.hubspot_deal_prop_sync_with_xero: False,
        },
    )


def sync_deal_from_xero(
    settings: Settings,
    deal_id: str,
    *,
    require_sync_flag: bool = True,
) -> SyncDealXeroResult:
    hs = HubSpotClient(settings.hubspot_access_token)
    extra = deal_xero_sync_read_property_names(settings)
    deal = hs.get_deal(deal_id, extra_properties=extra)
    props = deal.get("properties") or {}

    flag_raw = props.get(settings.hubspot_deal_prop_sync_with_xero)
    if require_sync_flag and not _hs_bool_true(flag_raw):
        return SyncDealXeroResult(ok=True, deal_id=deal_id, skipped=True)

    inv_id = (props.get(settings.hubspot_deal_prop_xero_invoice_id) or "").strip()
    inv_num_hs = (props.get(settings.hubspot_deal_prop_xero_invoice_number) or "").strip()

    try:
        xero = make_xero_client(settings)
    except ValueError as e:
        _patch_sync_error(hs, settings, deal_id, str(e))
        return SyncDealXeroResult(ok=False, deal_id=deal_id, error=str(e))

    try:
        inv: Optional[dict[str, Any]] = None
        if inv_id:
            inv = xero.get_invoice(inv_id)
            if not inv or not inv.get("InvoiceID"):
                inv = None
        if inv is None and inv_num_hs:
            inv = xero.get_invoice_by_number(inv_num_hs)
        if not inv:
            msg = (
                "No Xero invoice found. Set xero_invoice_id (Xero UUID) and/or invoice_number on the deal."
            )
            _patch_sync_error(hs, settings, deal_id, msg)
            return SyncDealXeroResult(ok=False, deal_id=deal_id, error=msg)

        num, status = invoice_fields_for_hubspot(inv)
        xid = str(inv.get("InvoiceID") or "").strip()

        patch_deal_xero(
            hs,
            settings,
            deal_id,
            {
                settings.hubspot_deal_prop_xero_invoice_number: num,
                settings.hubspot_deal_prop_xero_invoice_status: status,
                settings.hubspot_deal_prop_xero_invoice_id: xid,
                settings.hubspot_deal_prop_last_xero_sync: _utc_now_iso(),
                settings.hubspot_deal_prop_sync_with_xero: False,
                settings.hubspot_deal_prop_xero_last_error: "",
                settings.hubspot_deal_prop_xero_sync_last_error_date: "",
            },
        )
        return SyncDealXeroResult(ok=True, deal_id=deal_id)
    except Exception as e:
        err = str(e)
        _patch_sync_error(hs, settings, deal_id, err)
        return SyncDealXeroResult(ok=False, deal_id=deal_id, error=err)


def process_deals_pending_xero_sync(settings: Settings, *, max_deals: int = 50) -> dict[str, Any]:
    """Find deals with sync_with_xero=true and run sync for each (for cron)."""
    hs = HubSpotClient(settings.hubspot_access_token)
    extra = deal_xero_sync_read_property_names(settings)
    prop = settings.hubspot_deal_prop_sync_with_xero
    rows = hs.search_deals_property_eq(
        prop,
        "true",
        extra_properties=extra,
        limit=max_deals,
    )
    results: list[dict[str, Any]] = []
    for row in rows:
        did = str(row.get("id") or "")
        if not did:
            continue
        r = sync_deal_from_xero(settings, did, require_sync_flag=False)
        results.append(
            {
                "deal_id": did,
                "ok": r.ok,
                "skipped": r.skipped,
                "error": r.error,
            }
        )
    return {"queued": len(rows), "results": results}
