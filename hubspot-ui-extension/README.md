# HubSpot UI extension (deal central tab)

This folder is a **HubSpot developer project** that adds an **app card** on deal records in the **middle column** (`crm.record.tab`). The card links to your **Python bridge** with `?deal_id=<current deal>` so the wizard opens on the right deal.

The bridge itself still runs on your machine or server (FastAPI). HubSpot only hosts this small React card.

To expose **localhost** to HubSpot with HTTPS, use **ngrok** (or similar). Step-by-step: [docs/ngrok-tunnel.md](../docs/ngrok-tunnel.md).

**Railway / public deploy:** set **`BRIDGE_AUTH_TOKEN`** on the server and the same value as **`BRIDGE_AUTH_TOKEN`** in `XeroBridgeInvoice.jsx`, enable **`BRIDGE_COOKIE_SECURE=true`**, then `hs project upload`. See [docs/railway-security.md](../docs/railway-security.md).

## Prerequisites

1. [Node.js 18+](https://nodejs.org/) and npm.
2. [HubSpot CLI](https://developers.hubspot.com/docs/developer-tooling/local-development/hubspot-cli/install-the-cli) v7.6+ (`npm install -g @hubspot/cli@latest`).
3. A **developer account** (or app developer access) in HubSpot.
4. A **public HTTPS URL** for the bridge (e.g. [ngrok](https://ngrok.com), Cloudflare Tunnel, or a host). The bridge must use the same URL you put in the card (see below).

## One-time setup

### 1. Point the card at your bridge

Edit `src/app/cards/XeroBridgeInvoice.jsx` and set **`BRIDGE_BASE_URL`** to your bridge’s **HTTPS** origin (no trailing slash required; the code strips one).

```javascript
const BRIDGE_BASE_URL = "https://YOUR-SUBDOMAIN.ngrok-free.app";
```

### 2. Optional: branding and support

- In `src/app/app-hsmeta.json`, update **`support`** (email, URLs, phone) to your real details before listing or sharing widely.

### 3. Install card dependencies

From **this directory** (`hubspot-ui-extension/`):

```bash
hs project install-deps
```

### 4. Authenticate the CLI

```bash
hs account auth
```

Follow the browser flow and pick your **developer** HubSpot account.

### 5. Upload the project

```bash
hs project upload
```

First upload may prompt to create the project in HubSpot.

### 6. Install the app

In HubSpot: **Development → Projects →** your project **→** your app **→ Distribution → Install** (test account or target portal).

Grant **`crm.objects.deals.read`** when prompted.

### 7. Add the card to the deal record view

1. Open **CRM → Deals** and open any deal.
2. In the **middle column**, click **Customize** (or **Customize record**).
3. Choose the tab where you want the card (or create a tab).
4. Use **+** to add a card → **Card library** → filter **App** → add **Xero invoice bridge**.
5. **Save**.

### 8. Local dev (optional)

With the app installed in a test account:

```bash
hs project dev
```

Reload a deal record; the card should show a **Developing locally** tag while the dev server runs.

## Run the bridge

Expose your FastAPI app on the **same** URL as `BRIDGE_BASE_URL` (e.g. `ngrok http 8080` while `run.py` listens on 8080). Complete Xero OAuth and HubSpot token setup on the bridge as in the main repo README / `.env.example`.

## Validate without uploading

```bash
hs project validate
```

## Troubleshooting

| Issue | What to check |
| --- | --- |
| “There was a problem displaying this content” | UI extensions **cannot use HTML** (`<strong>`, `<br>`, etc.). Use HubSpot `Text` with `format` or nested `Text` only. |
| “Bridge URL not set” with a real ngrok URL | Old card build or overly strict placeholder check — run `hs project upload` after setting `BRIDGE_BASE_URL`; the card only treats the exact template host `your-bridge.example.com` as unset. |
| Link does nothing or wrong URL | `BRIDGE_BASE_URL` in `XeroBridgeInvoice.jsx` and rebuild/upload (`hs project upload`). |
| Card not in library | App installed and scopes granted; try **Customize** on a deal record again. |
| Bridge loads but deal not pre-selected | Bridge must be recent enough to support `?deal_id=` on `/` (see main `static/index.html`). |

## Security note

This card only opens a **URL** to your bridge. It does **not** send your HubSpot private app token to the browser. Keep the bridge authenticated and HTTPS-only in production.
