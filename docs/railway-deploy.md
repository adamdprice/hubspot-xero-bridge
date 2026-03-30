# Deploy the bridge on Railway

## 1. Prepare the repo

From your machine (with the project at `hubspot-xero-bridge/`):

```bash
cd /path/to/hubspot-xero-bridge
git init
git add .
git commit -m "HubSpot–Xero bridge"
```

Create a **private** GitHub repo, add it as `origin`, and push:

```bash
git remote add origin https://github.com/YOUR_ORG/hubspot-xero-bridge.git
git branch -M main
git push -u origin main
```

## 2. Create a Railway service

1. Open [Railway](https://railway.app) → **New project** → **Deploy from GitHub repo**.
2. Pick **`hubspot-xero-bridge`** (or your repo name).
3. Railway detects **Python** via `requirements.txt` and uses the **`Procfile`** / **`railway.toml`** start command:
   - `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Under the service **Settings → Deploy**, confirm **Root directory** is empty (repo root) unless this app lives in a subfolder.

## 3. Set environment variables

In Railway → your service → **Variables**, add everything the app needs (same as local `.env`), including:

| Variable | Notes |
|----------|--------|
| `HUBSPOT_ACCESS_TOKEN` | Private app token |
| `XERO_CLIENT_ID` / `XERO_CLIENT_SECRET` | Xero OAuth app |
| `XERO_REFRESH_TOKEN` / `XERO_TENANT_ID` | After OAuth |
| `XERO_REDIRECT_URI` | **Must** be `https://YOUR-RAILWAY-URL.up.railway.app/auth/xero/callback` (use your real Railway domain) |
| `BRIDGE_AUTH_TOKEN` | `openssl rand -hex 32` — same value in HubSpot `XeroBridgeInvoice.jsx` as `BRIDGE_AUTH_TOKEN` |
| `BRIDGE_COOKIE_SECURE` | `true` |
| `HUBSPOT_DEAL_SYNC_ENABLED` | `true` for production (omit to default true). Use `false` only before deal properties exist |

Optional: `HUBSPOT_DEAL_PROP_*`, `XERO_*` line defaults — see `.env.example`.

**After the first deploy**, copy the public **HTTPS URL** Railway assigns (e.g. `https://something.up.railway.app`). Put that origin in:

- `XERO_REDIRECT_URI` (full callback path as above)
- Xero developer portal → your app → **Redirect URIs** (same URL)
- `hubspot-ui-extension/.../XeroBridgeInvoice.jsx` → `BRIDGE_BASE_URL`
- Same file → `BRIDGE_AUTH_TOKEN` matching Railway

Then run **`hs project upload`** for the HubSpot extension.

See [railway-security.md](railway-security.md) for why `BRIDGE_AUTH_TOKEN` matters.

## 4. Deploy

Railway builds on every push to the connected branch. The **health check** hits **`/health`** (no auth).

Open the Railway URL in a browser; you should get either the **sign-in** page (if `BRIDGE_AUTH_TOKEN` is set) or the wizard (if not).

## 5. Local vs production commands

| Environment | Command |
|-------------|---------|
| Your Mac | `python3 run.py` → `http://127.0.0.1:8080`, reload on |
| Railway | `uvicorn` from **Procfile** — listens on **0.0.0.0**, **no** reload |

## Troubleshooting

| Issue | What to check |
|-------|----------------|
| Build fails on `itsdangerous` | `requirements.txt` includes `itsdangerous`; redeploy after pull. |
| 502 / connection refused | Service crashed — check **Deployments → Logs**. |
| Health check failing | `/health` must return 200; auth does not apply to `/health`. |
| Xero OAuth redirect mismatch | `XERO_REDIRECT_URI` and Xero portal must match **exactly** (https, path, no trailing slash differences). |
