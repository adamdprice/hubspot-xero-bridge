"""
Xero Accounting API: OAuth2 refresh + Contacts + Invoices.
https://developer.xero.com/documentation/api/accounting/overview
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import httpx

_log = logging.getLogger(__name__)


def _retry_wait_seconds(response: httpx.Response, attempt_index: int) -> float:
    """
    Seconds to sleep before retrying after 429/503.
    Xero often sends Retry-After: 120 — honoring that literally pauses ~2 minutes on the first retry.
    We cap each wait (still respecting short server hints like 5s) and rely on multiple attempts instead.
    """
    ra = response.headers.get("Retry-After")
    if ra:
        try:
            sec = float(ra)
            max_per_wait = 30.0
            return min(max_per_wait, max(1.0, sec))
        except ValueError:
            pass
    return min(60.0, 1.0 * (2**attempt_index))


def _parse_money(val: Any) -> float:
    try:
        if val is None or val == "":
            return 0.0
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def invoice_status_ui_label(inv: dict[str, Any]) -> str:
    """
    Map Xero invoice API data to wording similar to the Xero web app (not the raw Status enum).

    Raw API uses values like AUTHORISED; the UI shows e.g. "Awaiting payment" for unpaid sales invoices.
    Uses Status plus AmountDue where relevant.
    """
    raw = inv.get("Status")
    st = "" if raw is None else str(raw).strip().upper()
    amount_due = _parse_money(inv.get("AmountDue"))

    if st == "VOIDED":
        return "Voided"
    if st == "DELETED":
        return "Deleted"
    if st == "DRAFT":
        return "Draft"
    if st == "SUBMITTED":
        return "Submitted"
    if st == "PAID":
        return "Paid"
    if st == "AUTHORISED":
        if amount_due > 1e-6:
            return "Awaiting payment"
        return "Paid"
    if st:
        return st.replace("_", " ").title()
    return ""


def invoice_fields_for_hubspot(inv: dict[str, Any]) -> tuple[str, str]:
    """Invoice number and Xero UI–style status label for HubSpot single-line text fields."""
    raw_n = inv.get("InvoiceNumber")
    num = "" if raw_n is None else str(raw_n).strip()
    st = invoice_status_ui_label(inv)
    return num, st


def xero_invoice_contact_id(inv: dict[str, Any]) -> str:
    """ContactID from the invoice's Contact block, for syncing to HubSpot xero_contact_id."""
    c = inv.get("Contact") or {}
    if isinstance(c, dict):
        return str(c.get("ContactID") or "").strip()
    return ""


class XeroClient:
    TOKEN_URL = "https://identity.xero.com/connect/token"
    API_BASE = "https://api.xero.com/api.xro/2.0"
    # Per-process gate: each sync path creates a new XeroClient; without this, min_interval would reset every call
    # and batch sync would hammer Xero (immediate 429). All Accounting API traffic shares this spacing.
    _accounting_api_gate: threading.Lock = threading.Lock()
    _accounting_api_last_at: float = 0.0

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        tenant_id: str,
        *,
        min_interval_seconds: float = 0.0,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = (refresh_token or "").strip()
        self.tenant_id = (tenant_id or "").strip()
        self._min_interval_seconds = max(0.0, float(min_interval_seconds))
        if not self.refresh_token or not self.tenant_id:
            raise ValueError(
                "Xero is not connected yet. Complete OAuth in the browser — open GET /auth/xero/start "
                "(or click “Connect Xero” in the UI). The token is saved on disk when the token store is "
                "enabled, or set XERO_REFRESH_TOKEN and XERO_TENANT_ID in the host environment."
            )
        self._access_token: Optional[str] = None
        self._access_expires_at: float = 0.0

    def _ensure_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._access_expires_at - 60:
            return self._access_token
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            r.raise_for_status()
            data = r.json()
        self._access_token = data["access_token"]
        # Xero returns expires_in (seconds)
        self._access_expires_at = now + float(data.get("expires_in", 1800))
        if data.get("refresh_token"):
            self.refresh_token = data["refresh_token"]
            try:
                from app.xero_token_store import save_refresh_token

                save_refresh_token(self.refresh_token)
            except Exception as e:
                _log.warning("Could not persist rotated Xero refresh token: %s", e)
        return self._access_token

    def _headers(self) -> dict[str, str]:
        token = self._ensure_token()
        return {
            "Authorization": f"Bearer {token}",
            "Xero-tenant-id": self.tenant_id,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _throttle(self) -> None:
        """Space out Accounting API calls to stay under Xero per-minute limits (reduces 429)."""
        if self._min_interval_seconds <= 0:
            return
        with self._accounting_api_gate:
            now = time.time()
            wait = self._accounting_api_last_at + self._min_interval_seconds - now
            if wait > 0:
                time.sleep(wait)
            self._accounting_api_last_at = time.time()

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[Any] = None,
        timeout: float = 30.0,
        max_attempts: int = 6,
    ) -> httpx.Response:
        """
        Xero throttles aggressively (429 Too Many Requests). Retry with backoff and Retry-After.
        """
        last: Optional[httpx.Response] = None
        for attempt in range(max_attempts):
            self._throttle()
            with httpx.Client(timeout=timeout) as client:
                r = client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=self._headers(),
                )
            last = r
            if r.status_code in (429, 503) and attempt < max_attempts - 1:
                wait = _retry_wait_seconds(r, attempt)
                _log.warning(
                    "Xero rate limit / unavailable (%s); waiting %.1fs before retry %s/%s",
                    r.status_code,
                    wait,
                    attempt + 2,
                    max_attempts,
                )
                time.sleep(wait)
                continue
            return r
        assert last is not None
        return last

    def find_contact_by_email(self, email: str) -> Optional[str]:
        email_norm = email.strip().lower()
        r = self._request(
            "GET",
            f"{self.API_BASE}/Contacts",
            params={"where": f'EmailAddress=="{email_norm}"'},
        )
        r.raise_for_status()
        data = r.json()
        contacts = data.get("Contacts") or []
        if not contacts:
            return None
        return str(contacts[0].get("ContactID"))

    def search_contacts(self, term: str, *, page: int = 1) -> list[dict[str, Any]]:
        """Search contacts by Xero searchTerm (name / email / company)."""
        term = (term or "").strip()
        if not term:
            return []
        try:
            r = self._request(
                "GET",
                f"{self.API_BASE}/Contacts",
                params={"searchTerm": term, "page": page},
            )
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPStatusError as e:
            snippet = (e.response.text or "")[:1200].strip()
            raise RuntimeError(
                f"Xero API HTTP {e.response.status_code}: {snippet or e.response.reason_phrase}"
            ) from e
        except httpx.RequestError as e:
            raise RuntimeError(f"Could not reach Xero API: {e}") from e
        except ValueError as e:
            raise RuntimeError(f"Xero returned invalid JSON: {e}") from e
        return data.get("Contacts") or []

    def get_contact_by_id(self, contact_id: str) -> Optional[dict[str, Any]]:
        r = self._request("GET", f"{self.API_BASE}/Contacts/{contact_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        contacts = data.get("Contacts") or []
        return contacts[0] if contacts else None

    def create_contact(
        self,
        name: str,
        email: str,
        phone: Optional[str] = None,
    ) -> str:
        payload: dict[str, Any] = {
            "Contacts": [
                {
                    "Name": name or email,
                    "EmailAddress": email.strip().lower(),
                }
            ]
        }
        if phone:
            payload["Contacts"][0]["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": phone}]
        r = self._request("POST", f"{self.API_BASE}/Contacts", json=payload)
        r.raise_for_status()
        data = r.json()
        contacts = data.get("Contacts") or []
        if not contacts:
            raise RuntimeError("Xero create contact returned no Contacts")
        return str(contacts[0]["ContactID"])

    def create_contact_company(
        self,
        company_name: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> str:
        """Create a customer contact using company name (common for B2B)."""
        payload: dict[str, Any] = {
            "Contacts": [
                {
                    "Name": company_name.strip() or "Unknown",
                    "IsCustomer": True,
                }
            ]
        }
        if email:
            payload["Contacts"][0]["EmailAddress"] = email.strip().lower()
        if phone:
            payload["Contacts"][0]["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": phone}]
        r = self._request("POST", f"{self.API_BASE}/Contacts", json=payload)
        r.raise_for_status()
        data = r.json()
        contacts = data.get("Contacts") or []
        if not contacts:
            raise RuntimeError("Xero create contact returned no Contacts")
        return str(contacts[0]["ContactID"])

    def create_invoice_draft(
        self,
        contact_id: str,
        line_items: list[dict[str, Any]],
        reference: str,
        date_str: Optional[str] = None,
        due_date_str: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        line_items: Xero line format, e.g. [{"Description": "...", "Quantity": 1, "UnitAmount": 100, "AccountCode": "200"}]
        date_str / due_date_str: YYYY-MM-DD
        """
        inv: dict[str, Any] = {
            "Type": "ACCREC",
            "Contact": {"ContactID": contact_id},
            "LineItems": line_items,
            "Reference": reference[:255],
            "Status": "DRAFT",
        }
        if date_str:
            inv["Date"] = date_str
        if due_date_str:
            inv["DueDate"] = due_date_str
        r = self._request(
            "POST",
            f"{self.API_BASE}/Invoices",
            json={"Invoices": [inv]},
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()
        invoices = data.get("Invoices") or []
        if not invoices:
            raise RuntimeError("Xero create invoice returned no Invoices")
        return invoices[0]

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        r = self._request("GET", f"{self.API_BASE}/Invoices/{invoice_id}")
        r.raise_for_status()
        data = r.json()
        invoices = data.get("Invoices") or []
        return invoices[0] if invoices else {}

    def get_invoice_by_number(self, invoice_number: str) -> Optional[dict[str, Any]]:
        """Resolve a single invoice by invoice number (Xero where clause)."""
        n = (invoice_number or "").strip()
        if not n:
            return None
        # Escape double quotes in number for where clause
        safe = n.replace("\\", "\\\\").replace('"', '\\"')
        where = f'InvoiceNumber=="{safe}"'
        r = self._request(
            "GET",
            f"{self.API_BASE}/Invoices",
            params={"where": where},
        )
        r.raise_for_status()
        data = r.json()
        invoices = data.get("Invoices") or []
        if not invoices:
            return None
        return invoices[0]
