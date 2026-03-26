# HubSpot ↔ Xero bridge

Local FastAPI app to search HubSpot deals, pick or create Xero contacts, and create **draft** invoices.

## Run locally

```bash
python3 run.py
```

Open `http://127.0.0.1:8080`. Copy `.env.example` → `.env` and fill tokens.

## Deploy on Railway

See **[docs/railway-deploy.md](docs/railway-deploy.md)** (GitHub → Railway → env vars → Xero redirect).

Lock a public deployment with **`BRIDGE_AUTH_TOKEN`**: **[docs/railway-security.md](docs/railway-security.md)**.

## HubSpot deal tab (optional)

See **[hubspot-ui-extension/README.md](hubspot-ui-extension/README.md)** and `hs project upload`.

## Docs

| Doc | Topic |
|-----|--------|
| [docs/railway-deploy.md](docs/railway-deploy.md) | Build & deploy on Railway |
| [docs/railway-security.md](docs/railway-security.md) | `BRIDGE_AUTH_TOKEN`, cookies |
| [docs/ngrok-tunnel.md](docs/ngrok-tunnel.md) | Local HTTPS for HubSpot testing |
