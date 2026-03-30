"""
Optional HubSpot deal custom properties for Xero IDs / errors.
If properties are not created in HubSpot, set HUBSPOT_DEAL_SYNC_ENABLED=false in .env.
"""
from __future__ import annotations

from typing import Any

from app.config import Settings
from app.hubspot_client import HubSpotClient


def deal_xero_extra_property_names(settings: Settings) -> list[str]:
    """Property internal names to request on deal reads (empty when sync disabled)."""
    if not settings.hubspot_deal_sync_enabled:
        return []
    return [
        settings.hubspot_deal_prop_xero_contact_id,
        settings.hubspot_deal_prop_xero_invoice_id,
        settings.hubspot_deal_prop_xero_invoice_number,
        settings.hubspot_deal_prop_xero_invoice_status,
        settings.hubspot_deal_prop_xero_sync_key,
        settings.hubspot_deal_prop_xero_last_error,
        settings.hubspot_deal_prop_sync_with_xero,
        settings.hubspot_deal_prop_last_xero_sync,
        settings.hubspot_deal_prop_xero_sync_last_error_date,
    ]


def deal_xero_search_property_names(settings: Settings) -> list[str]:
    """Subset for search/billing UI (invoice + contact link)."""
    if not settings.hubspot_deal_sync_enabled:
        return []
    return [
        settings.hubspot_deal_prop_xero_invoice_id,
        settings.hubspot_deal_prop_xero_invoice_number,
        settings.hubspot_deal_prop_xero_invoice_status,
        settings.hubspot_deal_prop_xero_contact_id,
    ]


def deal_xero_manual_read_names(settings: Settings) -> list[str]:
    """Deal properties for manual invoice flow (no idempotency key on read)."""
    if not settings.hubspot_deal_sync_enabled:
        return []
    return [
        settings.hubspot_deal_prop_xero_contact_id,
        settings.hubspot_deal_prop_xero_invoice_id,
        settings.hubspot_deal_prop_xero_invoice_number,
        settings.hubspot_deal_prop_xero_invoice_status,
        settings.hubspot_deal_prop_xero_last_error,
    ]


def deal_xero_sync_read_property_names(settings: Settings) -> list[str]:
    """Properties needed to run Xero status sync (cron / sync-from-xero)."""
    if not settings.hubspot_deal_sync_enabled:
        return []
    return [
        settings.hubspot_deal_prop_xero_invoice_id,
        settings.hubspot_deal_prop_xero_invoice_number,
        settings.hubspot_deal_prop_xero_invoice_status,
        settings.hubspot_deal_prop_sync_with_xero,
        settings.hubspot_deal_prop_last_xero_sync,
        settings.hubspot_deal_prop_xero_last_error,
        settings.hubspot_deal_prop_xero_sync_last_error_date,
    ]


def patch_deal_xero(
    hs: HubSpotClient,
    settings: Settings,
    deal_id: str,
    properties: dict[str, Any],
) -> None:
    """Write Xero-related deal fields only when deal sync is enabled."""
    if not settings.hubspot_deal_sync_enabled:
        return
    hs.patch_deal(deal_id, properties)
