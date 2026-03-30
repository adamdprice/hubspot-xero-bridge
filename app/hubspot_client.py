"""
Minimal HubSpot CRM client for deal → contact → line items → products.
Matches patterns used elsewhere in your workspace (requests + retries).
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import requests


def hubspot_property_value_string(value: Any) -> str:
    """CRM v3 expects string values; booleans as 'true'/'false', datetimes as ISO 8601 strings."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _hubspot_error_message(response: requests.Response) -> str:
    try:
        body = response.json()
        msg = body.get("message") or body.get("error") or ""
        errs = body.get("errors")
        if isinstance(errs, list) and errs:
            first = errs[0]
            if isinstance(first, dict):
                msg = first.get("message") or first.get("context", {}).get("message") or msg
        if msg:
            return f"{response.status_code} HubSpot API: {msg}"
    except Exception:
        pass
    text = (response.text or "").strip()
    if text:
        return f"{response.status_code} HubSpot API: {text[:500]}"
    return f"{response.status_code} HubSpot API request failed"


class HubSpotClient:
    BASE = "https://api.hubapi.com"

    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token or os.getenv("HUBSPOT_ACCESS_TOKEN")
        if not self.access_token:
            raise ValueError("HUBSPOT_ACCESS_TOKEN is required")
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        })

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        url = f"{self.BASE}{path}" if path.startswith("/") else f"{self.BASE}/{path}"
        last_error: Optional[Exception] = None
        for attempt in range(4):
            try:
                r = self._session.request(method, url, json=json_body, params=params, timeout=60)
                r.raise_for_status()
                if r.text:
                    return r.json()
                return {}
            except requests.HTTPError as e:
                last_error = e
                if e.response is not None and e.response.status_code == 429 and attempt < 3:
                    time.sleep(2.0 ** (attempt + 1))
                    continue
                if e.response is not None and 400 <= e.response.status_code < 500:
                    msg = _hubspot_error_message(e.response)
                    raise requests.HTTPError(msg, response=e.response) from e
                raise
        if last_error is not None:
            raise last_error
        return {}

    def get_deal(self, deal_id: str, extra_properties: Optional[list[str]] = None) -> dict:
        props = [
            "dealname",
            "amount",
            "closedate",
            "pipeline",
            "dealstage",
            "hs_object_id",
        ]
        if extra_properties:
            props = list(dict.fromkeys(props + extra_properties))
        # Do not pass `associations` here — it can trigger 400 on some accounts; use v4 association routes instead.
        params = {"properties": ",".join(props)}
        return self._request("GET", f"/crm/v3/objects/deals/{deal_id}", params=params)

    def get_deal_safe(
        self,
        deal_id: str,
        extra_properties: Optional[list[str]] = None,
    ) -> Optional[dict]:
        """Return deal or None if not found (404)."""
        try:
            return self.get_deal(deal_id, extra_properties=extra_properties)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    def patch_deal(self, deal_id: str, properties: dict[str, Any]) -> dict:
        # None → JSON null. "" → empty string (many select fields clear with this; see sync trigger clear settings).
        body_props: dict[str, Any] = {}
        for k, v in properties.items():
            if v is None:
                body_props[k] = None
            else:
                body_props[k] = hubspot_property_value_string(v)
        return self._request(
            "PATCH",
            f"/crm/v3/objects/deals/{deal_id}",
            json_body={"properties": body_props},
        )

    def get_deal_associated_contact_ids(self, deal_id: str) -> list[str]:
        """Primary contact(s) on the deal via v4 associations."""
        data = self._request(
            "GET",
            f"/crm/v4/objects/deals/{deal_id}/associations/contacts",
        )
        out: list[str] = []
        for row in data.get("results", []):
            cid = row.get("toObjectId")
            if cid is not None:
                out.append(str(cid))
        return out

    def get_deal_line_item_ids(self, deal_id: str) -> list[str]:
        data = self._request(
            "GET",
            f"/crm/v4/objects/deals/{deal_id}/associations/line_items",
        )
        out: list[str] = []
        for row in data.get("results", []):
            lid = row.get("toObjectId")
            if lid is not None:
                out.append(str(lid))
        return out

    def batch_read_line_items(
        self,
        line_item_ids: list[str],
        properties: Optional[list[str]] = None,
    ) -> list[dict]:
        if not line_item_ids:
            return []
        props = properties or [
            "name",
            "quantity",
            "price",
            "amount",
            "hs_product_id",
        ]
        chunk_size = 100
        results: list[dict] = []
        for i in range(0, len(line_item_ids), chunk_size):
            chunk = line_item_ids[i : i + chunk_size]
            body = {
                "inputs": [{"id": x} for x in chunk],
                "properties": props,
            }
            data = self._request("POST", "/crm/v3/objects/line_items/batch/read", json_body=body)
            results.extend(data.get("results", []))
        return results

    def get_line_item_product_ids(self, line_item_ids: list[str]) -> dict[str, Optional[str]]:
        """line_item_id -> hs_product_id (HubSpot product object id)."""
        rows = self.batch_read_line_items(line_item_ids, properties=["hs_product_id"])
        out: dict[str, Optional[str]] = {}
        for row in rows:
            lid = str(row.get("id", ""))
            props = row.get("properties") or {}
            pid = props.get("hs_product_id")
            out[lid] = str(pid) if pid else None
        return out

    def batch_read_products(self, product_ids: list[str], properties: Optional[list[str]] = None) -> dict[str, dict[str, Any]]:
        if not product_ids:
            return {}
        props = properties or ["name", "description", "price", "hs_sku"]
        uniq = list(dict.fromkeys(product_ids))
        chunk_size = 100
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(uniq), chunk_size):
            chunk = uniq[i : i + chunk_size]
            body = {"inputs": [{"id": x} for x in chunk], "properties": props}
            data = self._request("POST", "/crm/v3/objects/products/batch/read", json_body=body)
            for row in data.get("results", []):
                pid = row.get("id")
                if pid is not None:
                    out[str(pid)] = row.get("properties") or {}
        return out

    def get_contact(self, contact_id: str, properties: Optional[list[str]] = None) -> dict:
        props = properties or ["firstname", "lastname", "email", "phone", "company"]
        params = {"properties": ",".join(props)}
        return self._request("GET", f"/crm/v3/objects/contacts/{contact_id}", params=params)

    def search_contacts_by_email(self, email: str) -> Optional[str]:
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": email.strip().lower(),
                        }
                    ]
                }
            ],
            "properties": ["email"],
            "limit": 1,
        }
        data = self._request("POST", "/crm/v3/objects/contacts/search", json_body=body)
        results = data.get("results") or []
        if not results:
            return None
        return str(results[0].get("id"))

    def search_deals(
        self,
        query: str,
        *,
        limit: int = 25,
        extra_properties: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Search deals by deal name (token contains). Empty query returns [].
        """
        q = (query or "").strip()
        if not q:
            return []
        props = [
            "dealname",
            "amount",
            "closedate",
            "pipeline",
            "dealstage",
            "hs_object_id",
        ]
        if extra_properties:
            props = list(dict.fromkeys(props + extra_properties))
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "dealname",
                            "operator": "CONTAINS_TOKEN",
                            "value": q,
                        }
                    ]
                }
            ],
            "properties": props,
            "limit": min(max(limit, 1), 100),
        }
        data = self._request("POST", "/crm/v3/objects/deals/search", json_body=body)
        return data.get("results") or []

    def search_deals_property_eq(
        self,
        property_name: str,
        value: str,
        *,
        extra_properties: Optional[list[str]] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Search deals where a property equals a string value (e.g. boolean 'true')."""
        props = [
            "dealname",
            "amount",
            "hs_object_id",
        ]
        if extra_properties:
            props = list(dict.fromkeys(props + extra_properties))
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": property_name,
                            "operator": "EQ",
                            "value": value,
                        }
                    ]
                }
            ],
            "properties": props,
            "limit": min(max(limit, 1), 100),
        }
        data = self._request("POST", "/crm/v3/objects/deals/search", json_body=body)
        return data.get("results") or []

    def search_deals_has_property(
        self,
        property_name: str,
        *,
        extra_properties: Optional[list[str]] = None,
        limit: int = 100,
        after: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        """
        Search deals where the property has a non-empty value (HubSpot HAS_PROPERTY operator).
        Returns (results, next_page_after_cursor) for pagination.
        """
        pn = (property_name or "").strip()
        if not pn:
            return [], None
        props = [
            "dealname",
            "amount",
            "hs_object_id",
        ]
        if extra_properties:
            props = list(dict.fromkeys(props + extra_properties))
        body: dict[str, Any] = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": pn,
                            "operator": "HAS_PROPERTY",
                        }
                    ]
                }
            ],
            "properties": props,
            "limit": min(max(limit, 1), 100),
        }
        if after:
            body["after"] = after
        data = self._request("POST", "/crm/v3/objects/deals/search", json_body=body)
        results = data.get("results") or []
        paging = data.get("paging") or {}
        next_after = paging.get("next", {}).get("after")
        return results, str(next_after) if next_after else None

    def get_deal_associated_company_ids(self, deal_id: str) -> list[str]:
        data = self._request(
            "GET",
            f"/crm/v4/objects/deals/{deal_id}/associations/companies",
        )
        out: list[str] = []
        for row in data.get("results", []):
            cid = row.get("toObjectId")
            if cid is not None:
                out.append(str(cid))
        return out

    def get_company(self, company_id: str, properties: Optional[list[str]] = None) -> dict:
        props = properties or ["name", "domain", "phone"]
        params = {"properties": ",".join(props)}
        return self._request("GET", f"/crm/v3/objects/companies/{company_id}", params=params)
