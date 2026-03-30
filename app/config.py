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

    # HubSpot deal property internal names (defaults match this portal; override via HUBSPOT_DEAL_PROP_* env if needed).
    # xero_contact_id, xero_invoice_id, xero_sync_trigger, last_xero_sync, xero_sync_last_error,
    # xero_sync_idempotency_key, xero_sync_last_error_date, invoice_number, invoice_status
    hubspot_deal_prop_xero_contact_id: str = "xero_contact_id"
    hubspot_deal_prop_xero_invoice_id: str = "xero_invoice_id"
    hubspot_deal_prop_xero_invoice_number: str = "invoice_number"
    hubspot_deal_prop_xero_invoice_status: str = "invoice_status"
    hubspot_deal_prop_xero_sync_key: str = "xero_sync_idempotency_key"
    hubspot_deal_prop_xero_last_error: str = "xero_sync_last_error"
    # Optional boolean property; leave empty if you removed it and only use xero_sync_trigger (dropdown).
    # Legacy: set to sync_with_xero if you still have that checkbox.
    hubspot_deal_prop_sync_with_xero: str = ""
    # Dropdown alternative (e.g. single option "Sync") for workflow triggers that cannot use booleans
    hubspot_deal_prop_xero_sync_trigger: str = "xero_sync_trigger"
    hubspot_deal_xero_sync_trigger_value: str = "Sync"
    # After sync, clear the trigger: leave unset so the API sends null (best for dropdowns). If HubSpot rejects null,
    # add a second option (e.g. "—") and set this to that option's internal value.
    hubspot_deal_xero_sync_trigger_clear_value: str = ""
    hubspot_deal_prop_last_xero_sync: str = "last_xero_sync"
    hubspot_deal_prop_xero_sync_last_error_date: str = "xero_sync_last_error_date"

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
