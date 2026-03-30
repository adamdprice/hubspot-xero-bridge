"""
Manual draft invoice: one line with configured ItemCode, account 200, VAT (TaxType), from HubSpot deal context.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.config import Settings
from app.deal_sync import deal_xero_manual_read_names, patch_deal_xero
from app.hubspot_client import HubSpotClient
from app.xero_credentials import make_xero_client
from app.xero_client import XeroClient, invoice_fields_for_hubspot


def _contact_person_name(props: dict[str, Any]) -> tuple[str, str]:
    first = (props.get("firstname") or "").strip()
    last = (props.get("lastname") or "").strip()
    email = (props.get("email") or "").strip()
    name = f"{first} {last}".strip() or email or "Unknown"
    return name, email


@dataclass
class ManualInvoiceResult:
    ok: bool
    deal_id: str
    xero_invoice_id: Optional[str] = None
    xero_contact_id: Optional[str] = None
    xero_invoice_number: Optional[str] = None
    xero_invoice_status: Optional[str] = None
    error: Optional[str] = None


def resolve_or_create_xero_contact(
    settings: Settings,
    hs: HubSpotClient,
    xero: XeroClient,
    deal_id: str,
    *,
    xero_contact_id: Optional[str],
    create_from_hubspot: bool,
) -> str:
    if xero_contact_id:
        existing = xero.get_contact_by_id(xero_contact_id.strip())
        if not existing:
            raise ValueError("Selected Xero contact was not found.")
        return xero_contact_id.strip()

    if not create_from_hubspot:
        raise ValueError("Choose an existing Xero contact or enable “Create from HubSpot”.")

    contact_ids = hs.get_deal_associated_contact_ids(deal_id)
    company_ids = hs.get_deal_associated_company_ids(deal_id)

    if contact_ids:
        contact = hs.get_contact(contact_ids[0])
        cprops = contact.get("properties") or {}
        name, email = _contact_person_name(cprops)
        phone = (cprops.get("phone") or "").strip() or None
        if email:
            found = xero.find_contact_by_email(email)
            if found:
                return found
            return xero.create_contact(name, email, phone=phone)
        # Contact without email: fall through to company if possible
    if company_ids:
        company = hs.get_company(company_ids[0])
        coprops = company.get("properties") or {}
        cname = (coprops.get("name") or "").strip() or "Unknown company"
        phone = (coprops.get("phone") or "").strip() or None
        return xero.create_contact_company(cname, email=None, phone=phone)

    raise ValueError(
        "This deal has no associated company or contact with enough detail. "
        "Associate a company (name) or a contact with an email in HubSpot, or pick an existing Xero contact."
    )


def create_manual_draft_invoice(
    settings: Settings,
    deal_id: str,
    *,
    unit_amount: float,
    quantity: float = 1.0,
    line_description: Optional[str] = None,
    xero_contact_id: Optional[str] = None,
    create_contact_from_hubspot: bool = False,
) -> ManualInvoiceResult:
    hs = HubSpotClient(settings.hubspot_access_token)
    deal = hs.get_deal_safe(deal_id, extra_properties=deal_xero_manual_read_names(settings))
    if not deal:
        return ManualInvoiceResult(ok=False, deal_id=deal_id, error="Deal not found in HubSpot.")

    props = deal.get("properties") or {}
    deal_name = (props.get("dealname") or f"Deal {deal_id}").strip()

    try:
        xero = make_xero_client(settings)
        xc_id = resolve_or_create_xero_contact(
            settings,
            hs,
            xero,
            deal_id,
            xero_contact_id=xero_contact_id,
            create_from_hubspot=create_contact_from_hubspot,
        )

        item_code = (settings.xero_item_code or "").strip()
        acct = (settings.xero_sales_account_code or "200").strip()
        tax = (settings.xero_line_tax_type or "").strip()

        line_desc = (line_description or "").strip() or deal_name
        line: dict[str, Any] = {
            "Description": line_desc[:4000],
            "Quantity": float(quantity),
            "UnitAmount": round(float(unit_amount), 4),
            "AccountCode": acct,
        }
        if item_code:
            line["ItemCode"] = item_code

        if tax:
            line["TaxType"] = tax

        reference = f"HS-{deal_id}-{deal_name}"[:255]
        inv = xero.create_invoice_draft(
            xc_id,
            [line],
            reference=reference,
        )
        inv_id = str(inv.get("InvoiceID"))
        inv_num, inv_status = invoice_fields_for_hubspot(inv)

        patch_deal_xero(
            hs,
            settings,
            deal_id,
            {
                settings.hubspot_deal_prop_xero_invoice_id: inv_id,
                settings.hubspot_deal_prop_xero_contact_id: xc_id,
                settings.hubspot_deal_prop_xero_invoice_number: inv_num,
                settings.hubspot_deal_prop_xero_invoice_status: inv_status,
                settings.hubspot_deal_prop_xero_last_error: "",
            },
        )

        return ManualInvoiceResult(
            ok=True,
            deal_id=deal_id,
            xero_invoice_id=inv_id,
            xero_contact_id=xc_id,
            xero_invoice_number=inv_num or None,
            xero_invoice_status=inv_status or None,
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
        return ManualInvoiceResult(ok=False, deal_id=deal_id, error=err)
