#!/bin/bash
# Double-click in Finder: starts ngrok tunnel to the bridge (default port 8080).
# Run the bridge first (start-local.command or: python3 run.py).
set -e
PORT="${PORT:-8080}"
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if ! command -v ngrok >/dev/null 2>&1; then
  echo "ngrok is not installed or not on PATH."
  echo "Install: https://ngrok.com/download  or: brew install ngrok/ngrok/ngrok"
  echo "Then run: ngrok config add-authtoken YOUR_TOKEN"
  read -r -p "Press Enter to close..."
  exit 1
fi

echo "Forwarding public HTTPS -> http://127.0.0.1:${PORT}"
echo "Copy the https://....ngrok-free.app URL into hubspot-ui-extension/src/app/cards/XeroBridgeInvoice.jsx as BRIDGE_BASE_URL"
echo ""
exec ngrok http "$PORT"
