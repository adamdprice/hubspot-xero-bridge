from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Empty is allowed at boot (e.g. Railway env not yet set); APIs check before calling HubSpot.
    hubspot_access_token: str = ""

    # Set false until you create the custom deal properties in HubSpot (see .env.example)
    hubspot_deal_sync_enabled: bool = True

    hubspot_deal_prop_xero_contact_id: str = "xero_contact_id"
    hubspot_deal_prop_xero_invoice_id: str = "xero_invoice_id"
    hubspot_deal_prop_xero_sync_key: str = "xero_sync_idempotency_key"
    hubspot_deal_prop_xero_last_error: str = "xero_sync_last_error"

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

    @field_validator("hubspot_deal_sync_enabled", mode="before")
    @classmethod
    def _parse_hubspot_deal_sync_enabled(cls, v: Any) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("0", "false", "no", "off"):
                return False
            if s in ("1", "true", "yes", "on"):
                return True
        return bool(v)


def get_settings() -> Settings:
    return Settings()
