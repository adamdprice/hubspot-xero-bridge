#!/bin/bash
# Double-click this file in Finder to start the HubSpot ↔ Xero bridge.
# (First run may create .venv and install dependencies.)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [[ ! -f .venv/bin/python ]]; then
  echo "Creating virtual environment (one-time)..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

echo "Starting server at http://127.0.0.1:8080 — press Ctrl+C to stop."
echo ""
exec .venv/bin/python run.py
