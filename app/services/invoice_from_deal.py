"""
Create a Xero draft invoice from a HubSpot deal (line items + billing contact).
Idempotency: if the deal already has xero_invoice_id set, returns without calling Xero again.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from app.config import Settings
from app.deal_sync import deal_xero_extra_property_names, patch_deal_xero
from app.hubspot_client import HubSpotClient
from app.xero_client import XeroClient


@dataclass
class InvoiceFromDealResult:
    ok: bool
    deal_id: str
    xero_invoice_id: Optional[str] = None
    xero_contact_id: Optional[str] = None
    idempotent: bool = False
    error: Optional[str] = None


def _contact_display_name(props: dict[str, Any]) -> tuple[str, str]:
    first = (props.get("firstname") or "").strip()
    last = (props.get("lastname") or "").strip()
    email = (props.get("email") or "").strip()
    name = f"{first} {last}".strip() or email or "Unknown"
    return name, email


def create_xero_invoice_from_deal(
    settings: Settings,
    deal_id: str,
    *,
    default_account_code: str = "200",
) -> InvoiceFromDealResult:
    hs = HubSpotClient(settings.hubspot_access_token)
    extra = deal_xero_extra_property_names(settings)
    deal = hs.get_deal(deal_id, extra_properties=extra)
    props = deal.get("properties") or {}

    existing_inv = (props.get(settings.hubspot_deal_prop_xero_invoice_id) or "").strip()
    if existing_inv:
        return InvoiceFromDealResult(
            ok=True,
            deal_id=deal_id,
            xero_invoice_id=existing_inv,
            xero_contact_id=(props.get(settings.hubspot_deal_prop_xero_contact_id) or "").strip() or None,
            idempotent=True,
        )

    if settings.hubspot_deal_sync_enabled:
        sync_key = (props.get(settings.hubspot_deal_prop_xero_sync_key) or "").strip()
        if not sync_key:
            sync_key = str(uuid.uuid4())
            patch_deal_xero(
                hs,
                settings,
                deal_id,
                {settings.hubspot_deal_prop_xero_sync_key: sync_key},
            )

    try:
        xero = XeroClient(
            settings.xero_client_id,
            settings.xero_client_secret,
            settings.xero_refresh_token,
            settings.xero_tenant_id,
        )

        contact_ids = hs.get_deal_associated_contact_ids(deal_id)
        if not contact_ids:
            raise ValueError("Deal has no associated contacts; associate a contact in HubSpot first.")
        contact = hs.get_contact(contact_ids[0])
        cprops = contact.get("properties") or {}
        name, email = _contact_display_name(cprops)
        if not email:
            raise ValueError("Primary contact has no email; required to match or create Xero contact.")

        xc_id = xero.find_contact_by_email(email)
        if not xc_id:
            phone = (cprops.get("phone") or "").strip() or None
            xc_id = xero.create_contact(name, email, phone=phone)

        line_ids = hs.get_deal_line_item_ids(deal_id)
        if not line_ids:
            raise ValueError("Deal has no line items.")

        li_rows = hs.batch_read_line_items(line_ids)
        product_ids_map = hs.get_line_item_product_ids(line_ids)
        all_pids = [pid for pid in product_ids_map.values() if pid]
        products_by_id = hs.batch_read_products(list(dict.fromkeys(all_pids))) if all_pids else {}

        xero_lines: list[dict[str, Any]] = []
        for row in li_rows:
            lid = str(row.get("id"))
            lip = row.get("properties") or {}
            qty = float(lip.get("quantity") or 1)
            price = lip.get("price")
            amount = lip.get("amount")
            unit = float(price) if price not in (None, "") else None
            if unit is None and amount not in (None, ""):
                unit = float(amount) / qty if qty else float(amount)
            if unit is None:
                unit = 0.0
            desc = (lip.get("name") or "Line").strip()
            pid = product_ids_map.get(lid)
            if pid and pid in products_by_id:
                p = products_by_id[pid]
                sku = (p.get("hs_sku") or "").strip()
                pname = (p.get("name") or "").strip()
                if pname:
                    desc = pname
                if sku:
                    desc = f"{desc} ({sku})"

            line: dict[str, Any] = {
                "Description": desc[:4000],
                "Quantity": qty,
                "UnitAmount": round(unit, 4),
                "AccountCode": default_account_code,
            }
            tt = (settings.xero_line_tax_type or "").strip()
            if tt:
                line["TaxType"] = tt
            xero_lines.append(line)

        deal_name = (props.get("dealname") or f"Deal {deal_id}").strip()
        reference = f"HS-{deal_id}-{deal_name}"[:255]

        inv = xero.create_invoice_draft(
            xc_id,
            xero_lines,
            reference=reference,
        )
        inv_id = str(inv.get("InvoiceID"))

        patch_deal_xero(
            hs,
            settings,
            deal_id,
            {
                settings.hubspot_deal_prop_xero_invoice_id: inv_id,
                settings.hubspot_deal_prop_xero_contact_id: xc_id,
                settings.hubspot_deal_prop_xero_last_error: "",
            },
        )

        return InvoiceFromDealResult(
            ok=True,
            deal_id=deal_id,
            xero_invoice_id=inv_id,
            xero_contact_id=xc_id,
            idempotent=False,
        )
    except Exception as e:
        err = str(e)
        try:
            patch_deal_xero(
                hs,
                settings,
                deal_id,
                {settings.hubspot_deal_prop_xero_last_error: err[:500]},
            )
        except Exception:
            pass
        return InvoiceFromDealResult(ok=False, deal_id=deal_id, error=err)
