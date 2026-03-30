"""
Pull invoice status (and number / Xero ID) from Xero into HubSpot when sync is requested.

Request sync via optional sync_with_xero (boolean), xero_sync_trigger dropdown (e.g. Sync), or a known xero_invoice_id.

Uses the deal's Xero invoice number and/or xero_invoice_id to find the invoice in Xero (number first when usable; ID fills/updates number on the deal).
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
from app.xero_client import invoice_fields_for_hubspot, xero_invoice_contact_id


def _clear_xero_sync_trigger_value(settings: Settings) -> Optional[str]:
    """
    After sync: clear the xero_sync_trigger dropdown.
    If HUBSPOT_DEAL_XERO_SYNC_TRIGGER_CLEAR_VALUE is set, use that option's internal value.
    Otherwise: empty string (default) or JSON null if hubspot_deal_xero_sync_trigger_clear_send_null is True.
    """
    raw = (settings.hubspot_deal_xero_sync_trigger_clear_value or "").strip()
    if raw:
        return raw
    if settings.hubspot_deal_xero_sync_trigger_clear_send_null:
        return None
    return ""


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _utc_today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _hs_bool_true(val: Any) -> bool:
    if val is True:
        return True
    s = str(val).strip().lower()
    return s in ("true", "1", "yes")


def _xero_invoice_number_ignore_tokens(settings: Settings) -> list[str]:
    raw = (settings.hubspot_xero_invoice_number_sync_ignore_values or "").strip()
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _xero_invoice_number_is_ignored(inv_num_hs: str, settings: Settings) -> bool:
    """True when the deal's invoice number field matches an ignored token (e.g. placeholder OLD)."""
    s = (inv_num_hs or "").strip()
    if not s:
        return False
    low = s.lower()
    return low in _xero_invoice_number_ignore_tokens(settings)


def _sync_requested(props: dict[str, Any], settings: Settings) -> bool:
    """True if the deal asks for a Xero pull (boolean, dropdown trigger, or known xero_invoice_id)."""
    sw = (settings.hubspot_deal_prop_sync_with_xero or "").strip()
    if sw and _hs_bool_true(props.get(sw)):
        return True
    raw = (props.get(settings.hubspot_deal_prop_xero_sync_trigger) or "").strip()
    want = (settings.hubspot_deal_xero_sync_trigger_value or "").strip()
    if raw and want and raw.lower() == want.lower():
        return True
    inv_id = (props.get(settings.hubspot_deal_prop_xero_invoice_id) or "").strip()
    if inv_id:
        return True
    return False


@dataclass
class SyncDealXeroResult:
    ok: bool
    deal_id: str
    skipped: bool = False
    error: Optional[str] = None


def _patch_sync_error(hs: HubSpotClient, settings: Settings, deal_id: str, message: str) -> None:
    """Record error on the deal. Does not clear xero_sync_trigger so you can fix data and retry without re-selecting Sync."""
    patch: dict[str, Any] = {
        settings.hubspot_deal_prop_xero_last_error: message[:500],
        settings.hubspot_deal_prop_xero_sync_last_error_date: _utc_today(),
    }
    sw = (settings.hubspot_deal_prop_sync_with_xero or "").strip()
    if sw:
        patch[sw] = False
    patch_deal_xero(hs, settings, deal_id, patch)


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

    if require_sync_flag and not _sync_requested(props, settings):
        return SyncDealXeroResult(ok=True, deal_id=deal_id, skipped=True)

    inv_id = (props.get(settings.hubspot_deal_prop_xero_invoice_id) or "").strip()
    inv_num_hs = (props.get(settings.hubspot_deal_prop_xero_invoice_number) or "").strip()

    # Ignored tokens (e.g. OLD) always skip — even if xero_invoice_id is set.
    if inv_num_hs and _xero_invoice_number_is_ignored(inv_num_hs, settings):
        return SyncDealXeroResult(ok=True, deal_id=deal_id, skipped=True)

    try:
        xero = make_xero_client(settings)
    except ValueError as e:
        _patch_sync_error(hs, settings, deal_id, str(e))
        return SyncDealXeroResult(ok=False, deal_id=deal_id, error=str(e))

    try:
        inv: Optional[dict[str, Any]] = None
        # Prefer invoice number when present (stale/wrong UUID would otherwise 404 and skip the number path).
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
                "No Xero invoice found. Set xero_invoice_id and/or a real Xero invoice number on the deal "
                "(number is looked up first when both are set)."
            )
            _patch_sync_error(hs, settings, deal_id, msg)
            return SyncDealXeroResult(ok=False, deal_id=deal_id, error=msg)

        num, status = invoice_fields_for_hubspot(inv)
        xid = str(inv.get("InvoiceID") or "").strip()
        xc = xero_invoice_contact_id(inv)

        # last_xero_sync is only set here (successful pull). Errors use _patch_sync_error and do not update this field.
        patch_ok: dict[str, Any] = {
            settings.hubspot_deal_prop_xero_invoice_number: num,
            settings.hubspot_deal_prop_xero_invoice_status: status,
            settings.hubspot_deal_prop_xero_invoice_id: xid,
            settings.hubspot_deal_prop_last_xero_sync: _utc_now_iso(),
            settings.hubspot_deal_prop_xero_sync_trigger: _clear_xero_sync_trigger_value(settings),
            settings.hubspot_deal_prop_xero_last_error: "",
            settings.hubspot_deal_prop_xero_sync_last_error_date: "",
        }
        if xc:
            patch_ok[settings.hubspot_deal_prop_xero_contact_id] = xc
        sw = (settings.hubspot_deal_prop_sync_with_xero or "").strip()
        if sw:
            patch_ok[sw] = False

        patch_deal_xero(hs, settings, deal_id, patch_ok)
        return SyncDealXeroResult(ok=True, deal_id=deal_id)
    except Exception as e:
        err = str(e)
        _patch_sync_error(hs, settings, deal_id, err)
        return SyncDealXeroResult(ok=False, deal_id=deal_id, error=err)


def process_deals_pending_xero_sync(settings: Settings, *, max_deals: int = 50) -> dict[str, Any]:
    """Find deals with sync_with_xero=true (if that property exists) and/or xero_sync_trigger=Sync; run each (cron)."""
    hs = HubSpotClient(settings.hubspot_access_token)
    extra = deal_xero_sync_read_property_names(settings)
    seen: dict[str, dict[str, Any]] = {}
    sw = (settings.hubspot_deal_prop_sync_with_xero or "").strip()
    if sw:
        for row in hs.search_deals_property_eq(
            sw,
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


def _deal_row_skip_for_invoice_batch_sync(
    row: dict[str, Any],
    *,
    prop: str,
    settings: Settings,
) -> bool:
    """Skip when invoice number matches an ignored token (e.g. OLD), regardless of xero_invoice_id."""
    props = row.get("properties") or {}
    inv_raw = (props.get(prop) or "").strip() if prop else ""
    if inv_raw and _xero_invoice_number_is_ignored(inv_raw, settings):
        return True
    return False


def process_deals_with_xero_invoice_number_sync(
    settings: Settings,
    *,
    max_deals: int = 150,
) -> dict[str, Any]:
    """
    Find deals with xero_invoice_number and/or xero_invoice_id set (HubSpot HAS_PROPERTY) and pull from Xero.
    Does not require xero_sync_trigger or sync_with_xero — for scheduled / cron sync.
    """
    if settings.hubspot_xero_invoice_number_sync_disabled:
        return {
            "queued": 0,
            "results": [],
            "disabled": True,
            "reason": "hubspot_xero_invoice_number_sync_disabled",
        }
    prop = (settings.hubspot_deal_prop_xero_invoice_number or "").strip()
    id_prop = (settings.hubspot_deal_prop_xero_invoice_id or "").strip()
    if not prop and not id_prop:
        return {
            "queued": 0,
            "results": [],
            "error": "configure hubspot_deal_prop_xero_invoice_number and/or xero_invoice_id",
        }

    hs = HubSpotClient(settings.hubspot_access_token)
    extra = deal_xero_sync_read_property_names(settings)
    seen_ids: set[str] = set()
    deal_ids: list[str] = []

    def _append_row(row: dict[str, Any]) -> None:
        did = str(row.get("id") or "").strip()
        if not did or did in seen_ids:
            return
        if prop and _deal_row_skip_for_invoice_batch_sync(row, prop=prop, settings=settings):
            return
        seen_ids.add(did)
        deal_ids.append(did)

    def _paginate_has_property(property_name: str) -> None:
        if not property_name or len(deal_ids) >= max_deals:
            return
        after: Optional[str] = None
        while len(deal_ids) < max_deals:
            page_limit = min(100, max_deals - len(deal_ids))
            batch, next_after = hs.search_deals_has_property(
                property_name,
                extra_properties=extra,
                limit=page_limit,
                after=after,
            )
            for row in batch:
                if len(deal_ids) >= max_deals:
                    break
                _append_row(row)
            if not next_after or not batch:
                break
            after = next_after

    if prop:
        _paginate_has_property(prop)
    if id_prop:
        _paginate_has_property(id_prop)

    results: list[dict[str, Any]] = []
    for did in deal_ids:
        r = sync_deal_from_xero(settings, did, require_sync_flag=False)
        results.append(
            {
                "deal_id": did,
                "ok": r.ok,
                "skipped": r.skipped,
                "error": r.error,
            }
        )
    return {"queued": len(deal_ids), "results": results}
