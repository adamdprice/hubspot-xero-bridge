#!/usr/bin/env python3
"""
Create the deal properties expected by hubspot-xero-bridge (Xero IDs, sync, invoice display).

Requires HUBSPOT_ACCESS_TOKEN in .env (or env) with scope:
  crm.schemas.deals.write
(Add it under your private app → Scopes → CRM → Deals → "Deal schema" write / similar.)

Usage (from repo root):
  cd hubspot-xero-bridge
  set -a && source .env && set +a
  python3 scripts/create_hubspot_deal_properties.py

Or:
  HUBSPOT_ACCESS_TOKEN=pat-xxx python3 scripts/create_hubspot_deal_properties.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

# Load .env from project root (parent of scripts/)
_ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

BASE = "https://api.hubapi.com"

# Standard deal group; use another if your portal uses a custom group (see GET /crm/v3/properties/deals/groups)
GROUP = "dealinformation"

TEXT = {"type": "string", "fieldType": "text"}
BOOL = {"type": "bool", "fieldType": "booleancheckbox"}
DATETIME = {"type": "datetime", "fieldType": "date"}
DATE = {"type": "date", "fieldType": "date"}

PROPERTIES = [
    {
        "name": "xero_contact_id",
        "label": "Xero contact ID",
        "description": "Xero ContactID synced by the HubSpot–Xero bridge.",
        "field": TEXT,
    },
    {
        "name": "xero_invoice_id",
        "label": "Xero invoice ID",
        "description": "Latest Xero InvoiceID for this deal (bridge).",
        "field": TEXT,
    },
    {
        "name": "xero_invoice_number",
        "label": "Invoice number (Xero)",
        "description": "Xero InvoiceNumber (e.g. INV-0001), synced by the bridge. Do not use internal name invoice_number — HubSpot may treat it as read-only.",
        "field": TEXT,
    },
    {
        "name": "xero_invoice_status",
        "label": "Invoice status (Xero)",
        "description": "Xero invoice Status (e.g. DRAFT, AUTHORISED), synced by the bridge.",
        "field": TEXT,
    },
    {
        "name": "xero_sync_idempotency_key",
        "label": "Xero sync idempotency key",
        "description": "Internal idempotency key for invoice sync (bridge).",
        "field": TEXT,
    },
    {
        "name": "xero_sync_last_error",
        "label": "Xero sync last error",
        "description": "Last sync error message from the bridge (if any).",
        "field": TEXT,
    },
    {
        "name": "sync_with_xero",
        "label": "Sync with Xero",
        "description": "When true, the bridge pulls invoice status from Xero (cron or sync endpoint).",
        "field": BOOL,
    },
    {
        "name": "last_xero_sync",
        "label": "Last Xero sync",
        "description": "When the deal was last synced from Xero (bridge).",
        "field": DATETIME,
    },
    {
        "name": "xero_sync_last_error_date",
        "label": "Xero sync last error date",
        "description": "Date of the last sync error from Xero (bridge).",
        "field": DATE,
    },
]


def main() -> int:
    token = (os.getenv("HUBSPOT_ACCESS_TOKEN") or "").strip()
    if not token:
        print("Set HUBSPOT_ACCESS_TOKEN (e.g. in .env).", file=sys.stderr)
        return 1

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    session = requests.Session()
    session.headers.update(headers)

    for p in PROPERTIES:
        name = p["name"]
        get_url = f"{BASE}/crm/v3/properties/deals/{name}"
        r = session.get(get_url, timeout=30)
        if r.status_code == 200:
            print(f"OK (already exists): {name}")
            continue
        if r.status_code not in (404,):
            print(f"GET {name}: {r.status_code} {r.text[:300]}", file=sys.stderr)

        field = p.get("field") or TEXT
        body = {
            "name": name,
            "label": p["label"],
            "groupName": GROUP,
            "description": p["description"],
            "hasUniqueValue": False,
            **field,
        }
        r = session.post(f"{BASE}/crm/v3/properties/deals", json=body, timeout=30)
        if r.status_code in (200, 201):
            print(f"Created: {name}")
            continue
        if r.status_code == 409:
            print(f"OK (already exists): {name}")
            continue
        print(f"FAIL {name}: {r.status_code} {r.text}", file=sys.stderr)
        return 1

    print("\nDone. Restart the bridge after properties are created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
