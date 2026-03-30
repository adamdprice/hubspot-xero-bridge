"""
Pull invoice status (and number / Xero ID) from Xero into HubSpot when sync is requested.

Request sync via sync_with_xero=true and/or xero_sync_trigger dropdown (default option value "Sync").

Uses invoice_number and/or xero_invoice_id to find the invoice in Xero (number is tried first when set).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import Settings
from app.deal_sync import deal_xero_sync_read_property_names, patch_deal_xero
from app.hubspot_client import HubSpotClient
from app.xero_credentials import make_xero_client
from app.xero_client import invoice_fields_for_hubspot


def _clear_xero_sync_trigger_value(settings: Settings) -> str:
    return (settings.hubspot_deal_xero_sync_trigger_clear_value or "").strip()


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _utc_today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _hs_bool_true(val: Any) -> bool:
    if val is True:
        return True
    s = str(val).strip().lower()
    return s in ("true", "1", "yes")


def _sync_requested(props: dict[str, Any], settings: Settings) -> bool:
    """True if the deal asks for a Xero pull (boolean and/or dropdown trigger)."""
    if _hs_bool_true(props.get(settings.hubspot_deal_prop_sync_with_xero)):
        return True
    raw = (props.get(settings.hubspot_deal_prop_xero_sync_trigger) or "").strip()
    want = (settings.hubspot_deal_xero_sync_trigger_value or "").strip()
    if not raw or not want:
        return False
    return raw.lower() == want.lower()


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
            settings.hubspot_deal_prop_xero_sync_trigger: _clear_xero_sync_trigger_value(settings),
        },
    )


def sync_deal_from_xero(
    settings: Settings,
    deal_id: str,
    *,
    require_sync_flag: bool = True,
) -> SyncDealXeroResult:
    if not settings.hubspot_deal_sync_enabled:
        return SyncDealXeroResult(
            ok=False,
            deal_id=deal_id,
            error=(
                "HUBSPOT_DEAL_SYNC_ENABLED is false — set to true in Railway so invoice fields "
                "can be written to the deal (otherwise sync runs but HubSpot is not updated)."
            ),
        )

    hs = HubSpotClient(settings.hubspot_access_token)
    extra = deal_xero_sync_read_property_names(settings)
    deal = hs.get_deal(deal_id, extra_properties=extra)
    props = deal.get("properties") or {}

    if require_sync_flag and not _sync_requested(props, settings):
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
        # Prefer invoice number when present (most users only have INV-… on the deal). Stale/wrong UUID
        # would otherwise 404 and skip the number path.
        if inv_num_hs:
            inv = xero.get_invoice_by_number(inv_num_hs)
        if inv is None and inv_id:
            try:
                inv = xero.get_invoice(inv_id)
                if not inv or not inv.get("InvoiceID"):
                    inv = None
            except httpx.HTTPStatusError as e:
                code = e.response.status_code if e.response is not None else None
                if code != 404:
                    raise
                inv = None
        if not inv:
            msg = (
                "No Xero invoice found. Set invoice_number and/or xero_invoice_id on the deal "
                "(number is looked up first when both are set)."
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
                settings.hubspot_deal_prop_xero_sync_trigger: _clear_xero_sync_trigger_value(settings),
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
    """Find deals with sync_with_xero=true and/or xero_sync_trigger=Sync; run sync for each (cron)."""
    hs = HubSpotClient(settings.hubspot_access_token)
    extra = deal_xero_sync_read_property_names(settings)
    seen: dict[str, dict[str, Any]] = {}
    for row in hs.search_deals_property_eq(
        settings.hubspot_deal_prop_sync_with_xero,
        "true",
        extra_properties=extra,
        limit=max_deals,
    ):
        did = str(row.get("id") or "")
        if did:
            seen[did] = row
    trig_val = (settings.hubspot_deal_xero_sync_trigger_value or "").strip()
    if trig_val:
        for row in hs.search_deals_property_eq(
            settings.hubspot_deal_prop_xero_sync_trigger,
            trig_val,
            extra_properties=extra,
            limit=max_deals,
        ):
            did = str(row.get("id") or "")
            if did:
                seen[did] = row
    rows = list(seen.values())[:max_deals]
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
