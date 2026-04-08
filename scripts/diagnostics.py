#!/usr/bin/env python3
"""
Run from the hubspot-xero-bridge directory (loads .env from cwd):

  python scripts/diagnostics.py
  python scripts/diagnostics.py --deal-id 123456789
  python scripts/diagnostics.py --skip-batch

Same checks as GET /api/diagnostics/pipeline — HubSpot read, Xero Organisation, batch deal id dry-run, optional per-deal Xero preview (no HubSpot PATCH).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    from app.config import get_settings
    from app.diagnostics import run_pipeline_diagnostics

    ap = argparse.ArgumentParser(description="HubSpot + Xero pipeline diagnostics (read-only preview).")
    ap.add_argument("--deal-id", default=None, help="HubSpot deal id for Xero invoice mapping preview")
    ap.add_argument("--max-deals", type=int, default=10, help="Max deals in batch dry-run list")
    ap.add_argument("--skip-batch", action="store_true", help="Skip HubSpot search dry-run")
    args = ap.parse_args()

    out = run_pipeline_diagnostics(
        get_settings(),
        deal_id=(args.deal_id or "").strip() or None,
        max_deals=max(1, min(args.max_deals, 200)),
        include_batch_preview=not args.skip_batch,
    )
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
