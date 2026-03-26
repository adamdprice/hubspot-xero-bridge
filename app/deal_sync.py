"""
Optional HubSpot deal custom properties for Xero IDs / errors.
If properties are not created in HubSpot, set HUBSPOT_DEAL_SYNC_ENABLED=false in .env.
"""
from __future__ import annotations

from app.config import Settings
from app.hubspot_client import HubSpotClient


def deal_xero_extra_property_names(settings: Settings) -> list[str]:
    """Property internal names to request on deal reads (empty when sync disabled)."""
    if not settings.hubspot_deal_sync_enabled:
        return []
    return [
        settings.hubspot_deal_prop_xero_contact_id,
        settings.hubspot_deal_prop_xero_invoice_id,
        settings.hubspot_deal_prop_xero_sync_key,
        settings.hubspot_deal_prop_xero_last_error,
    ]


def deal_xero_search_property_names(settings: Settings) -> list[str]:
    """Subset for search/billing UI (invoice + contact link)."""
    if not settings.hubspot_deal_sync_enabled:
        return []
    return [
        settings.hubspot_deal_prop_xero_invoice_id,
        settings.hubspot_deal_prop_xero_contact_id,
    ]


def deal_xero_manual_read_names(settings: Settings) -> list[str]:
    """Deal properties for manual invoice flow (no idempotency key on read)."""
    if not settings.hubspot_deal_sync_enabled:
        return []
    return [
        settings.hubspot_deal_prop_xero_contact_id,
        settings.hubspot_deal_prop_xero_invoice_id,
        settings.hubspot_deal_prop_xero_last_error,
    ]


def patch_deal_xero(
    hs: HubSpotClient,
    settings: Settings,
    deal_id: str,
    properties: dict[str, str],
) -> None:
    """Write Xero-related deal fields only when deal sync is enabled."""
    if not settings.hubspot_deal_sync_enabled:
        return
    hs.patch_deal(deal_id, properties)
