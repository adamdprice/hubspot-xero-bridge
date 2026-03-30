"""
HubSpot deal custom properties for Xero IDs / errors.
Create these properties in HubSpot before relying on the bridge (see .env.example / scripts).
"""
from __future__ import annotations

from typing import Any

from app.config import Settings
from app.hubspot_client import HubSpotClient


def deal_xero_extra_property_names(settings: Settings) -> list[str]:
    """Property internal names to request on deal reads."""
    out = [
        settings.hubspot_deal_prop_xero_contact_id,
        settings.hubspot_deal_prop_xero_invoice_id,
        settings.hubspot_deal_prop_xero_invoice_number,
        settings.hubspot_deal_prop_xero_invoice_status,
        settings.hubspot_deal_prop_xero_sync_key,
        settings.hubspot_deal_prop_xero_last_error,
    ]
    sw = (settings.hubspot_deal_prop_sync_with_xero or "").strip()
    if sw:
        out.append(sw)
    out.extend(
        [
            settings.hubspot_deal_prop_xero_sync_trigger,
            settings.hubspot_deal_prop_last_xero_sync,
            settings.hubspot_deal_prop_xero_sync_last_error_date,
        ]
    )
    return out


def deal_xero_search_property_names(settings: Settings) -> list[str]:
    """Subset for search/billing UI (invoice + contact link)."""
    return [
        settings.hubspot_deal_prop_xero_invoice_id,
        settings.hubspot_deal_prop_xero_invoice_number,
        settings.hubspot_deal_prop_xero_invoice_status,
        settings.hubspot_deal_prop_xero_contact_id,
    ]


def deal_xero_manual_read_names(settings: Settings) -> list[str]:
    """Deal properties for manual invoice flow (no idempotency key on read)."""
    return [
        settings.hubspot_deal_prop_xero_contact_id,
        settings.hubspot_deal_prop_xero_invoice_id,
        settings.hubspot_deal_prop_xero_invoice_number,
        settings.hubspot_deal_prop_xero_invoice_status,
        settings.hubspot_deal_prop_xero_last_error,
    ]


def deal_xero_sync_read_property_names(settings: Settings) -> list[str]:
    """Properties needed to run Xero status sync (cron / sync-from-xero)."""
    out = [
        settings.hubspot_deal_prop_xero_contact_id,
        settings.hubspot_deal_prop_xero_invoice_id,
        settings.hubspot_deal_prop_xero_invoice_number,
        settings.hubspot_deal_prop_xero_invoice_status,
    ]
    sw = (settings.hubspot_deal_prop_sync_with_xero or "").strip()
    if sw:
        out.append(sw)
    out.extend(
        [
            settings.hubspot_deal_prop_xero_sync_trigger,
            settings.hubspot_deal_prop_last_xero_sync,
            settings.hubspot_deal_prop_xero_last_error,
            settings.hubspot_deal_prop_xero_sync_last_error_date,
        ]
    )
    return out


def patch_deal_xero(
    hs: HubSpotClient,
    settings: Settings,
    deal_id: str,
    properties: dict[str, Any],
) -> None:
    """Write Xero-related deal fields."""
    hs.patch_deal(deal_id, properties)
