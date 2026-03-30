import logging
from typing import Any

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Empty is allowed at boot (e.g. Railway env not yet set); APIs check before calling HubSpot.
    hubspot_access_token: str = ""
    # Private/public app "Client secret" (Auth tab) — NOT the access token; validates X-HubSpot-Signature on webhooks.
    hubspot_client_secret: str = ""

    # HubSpot deal property internal names (override via HUBSPOT_DEAL_PROP_* env if needed).
    # Use xero_invoice_number / xero_invoice_status (not invoice_number / invoice_status): HubSpot may expose
    # invoice_number as read-only on deals, so the API cannot PATCH it.
    hubspot_deal_prop_xero_contact_id: str = "xero_contact_id"
    hubspot_deal_prop_xero_invoice_id: str = "xero_invoice_id"
    hubspot_deal_prop_xero_invoice_number: str = "xero_invoice_number"
    hubspot_deal_prop_xero_invoice_status: str = "xero_invoice_status"
    hubspot_deal_prop_xero_sync_key: str = "xero_sync_idempotency_key"
    hubspot_deal_prop_xero_last_error: str = "xero_sync_last_error"
    # Optional boolean property; leave empty if you removed it and only use xero_sync_trigger (dropdown).
    # Legacy: set to sync_with_xero if you still have that checkbox.
    hubspot_deal_prop_sync_with_xero: str = ""
    # Dropdown alternative (e.g. single option "Sync") for workflow triggers that cannot use booleans
    hubspot_deal_prop_xero_sync_trigger: str = "xero_sync_trigger"
    hubspot_deal_xero_sync_trigger_value: str = "Sync"
    # Must match the option value HubSpot stores (check webhook payload propertyValue — not always the label text).
    # After sync, clear the trigger. If set, PATCH that exact option value (e.g. second menu option "—").
    hubspot_deal_xero_sync_trigger_clear_value: str = ""
    # If clear_value is unset: False = send "" to clear dropdown; True = send JSON null. Some portals only accept one.
    hubspot_deal_xero_sync_trigger_clear_send_null: bool = False
    hubspot_deal_prop_last_xero_sync: str = "last_xero_sync"
    hubspot_deal_prop_xero_sync_last_error_date: str = "xero_sync_last_error_date"

    # In-process timer: pull Xero status for deals with hubspot_deal_prop_xero_invoice_number set (HAS_PROPERTY).
    # Set to 0 to disable the background loop (e.g. use Railway cron on /api/cron/sync-xero-by-invoice-number only).
    # Use a single uvicorn worker if you enable this, or each worker would run its own loop.
    hubspot_xero_invoice_number_sync_interval_seconds: int = 600
    hubspot_xero_invoice_number_sync_max_deals: int = 150
    # Temporarily stop invoice-number batch sync (timer + POST /api/cron/sync-xero-by-invoice-number). Webhook trigger sync unchanged.
    hubspot_xero_invoice_number_sync_disabled: bool = False
    # Comma-separated values (case-insensitive exact match). Deals with this Xero invoice number are never synced from Xero.
    hubspot_xero_invoice_number_sync_ignore_values: str = "OLD"

    # OAuth app credentials (always required for Xero API after you connect)
    xero_client_id: str = ""
    xero_client_secret: str = ""
    # Filled after browser OAuth — not shown in the Xero app UI until you complete authorization
    xero_refresh_token: str = ""
    xero_tenant_id: str = ""

    # Must match a redirect URI registered on your Xero app (use http://localhost:8080/... not 127.0.0.1)
    xero_redirect_uri: str = "http://localhost:8080/auth/xero/callback"

    # Public deploy (Railway, etc.): require ?token= or Authorization: Bearer (generate with openssl rand -hex 32)
    bridge_auth_token: str = ""
    # Optional; if unset, a key is derived from bridge_auth_token for signing session cookies
    bridge_session_secret: str = ""
    # Set true on HTTPS hosts (e.g. Railway) so session cookies are Secure
    bridge_cookie_secure: bool = False

    # Invoice line defaults (must match your Xero org: item code, tax type, account)
    xero_sales_account_code: str = Field(
        default="200",
        validation_alias=AliasChoices("XERO_SALES_ACCOUNT_CODE", "XERO_DEFAULT_ACCOUNT_CODE"),
    )
    xero_item_code: str = "Day Rate (VAT)"
    # Tax type for VAT on sales — org-specific. UK standard rate sales often OUTPUT2; verify in Xero Settings → Tax rates
    xero_line_tax_type: str = "OUTPUT2"
    # Minimum delay between Xero Accounting API calls (per process). Cuts 429s when syncing many deals. Try 1.0 if still rate-limited.
    xero_api_min_interval_seconds: float = 0.5

    @model_validator(mode="after")
    def _warn_if_webhook_secret_looks_like_access_token(self):
        s = (self.hubspot_client_secret or "").strip()
        if s.lower().startswith("pat-"):
            _log.warning(
                "HUBSPOT_CLIENT_SECRET looks like a Private App access token (pat-...). "
                "Webhook signatures need the separate Client secret on the same app's Auth tab — not the access token."
            )
        return self


def get_settings() -> Settings:
    return Settings()
