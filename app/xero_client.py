"""
Xero Accounting API: OAuth2 refresh + Contacts + Invoices.
https://developer.xero.com/documentation/api/accounting/overview
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

_log = logging.getLogger(__name__)


def invoice_fields_for_hubspot(inv: dict[str, Any]) -> tuple[str, str]:
    """Xero InvoiceNumber and Status for HubSpot single-line text fields."""
    raw_n = inv.get("InvoiceNumber")
    num = "" if raw_n is None else str(raw_n).strip()
    raw_s = inv.get("Status")
    st = "" if raw_s is None else str(raw_s).strip()
    return num, st


class XeroClient:
    TOKEN_URL = "https://identity.xero.com/connect/token"
    API_BASE = "https://api.xero.com/api.xro/2.0"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        tenant_id: str,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = (refresh_token or "").strip()
        self.tenant_id = (tenant_id or "").strip()
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

    def find_contact_by_email(self, email: str) -> Optional[str]:
        email_norm = email.strip().lower()
        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                f"{self.API_BASE}/Contacts",
                params={"where": f'EmailAddress=="{email_norm}"'},
                headers=self._headers(),
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
            with httpx.Client(timeout=30.0) as client:
                r = client.get(
                    f"{self.API_BASE}/Contacts",
                    params={"searchTerm": term, "page": page},
                    headers=self._headers(),
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
        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                f"{self.API_BASE}/Contacts/{contact_id}",
                headers=self._headers(),
            )
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
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{self.API_BASE}/Contacts",
                json=payload,
                headers=self._headers(),
            )
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
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{self.API_BASE}/Contacts",
                json=payload,
                headers=self._headers(),
            )
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
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{self.API_BASE}/Invoices",
                json={"Invoices": [inv]},
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json()
        invoices = data.get("Invoices") or []
        if not invoices:
            raise RuntimeError("Xero create invoice returned no Invoices")
        return invoices[0]

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                f"{self.API_BASE}/Invoices/{invoice_id}",
                headers=self._headers(),
            )
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
        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                f"{self.API_BASE}/Invoices",
                params={"where": where},
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json()
        invoices = data.get("Invoices") or []
        if not invoices:
            return None
        return invoices[0]
