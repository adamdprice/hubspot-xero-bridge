# Expose the bridge with ngrok (HTTPS for HubSpot)

The HubSpot deal card must open an **`https://…`** URL. While you develop, you can keep FastAPI on your Mac and use **ngrok** to publish a temporary public URL that forwards to `localhost:8080`.

## 1. Install ngrok

**macOS (Homebrew):**

```bash
brew install ngrok/ngrok/ngrok
```

**Or** download the installer from [ngrok download](https://ngrok.com/download) and put `ngrok` on your `PATH`.

## 2. Sign in and add your auth token (once)

1. Create a free account at [ngrok](https://dashboard.ngrok.com/signup).
2. In the dashboard, copy your **Authtoken**.
3. Run:

```bash
ngrok config add-authtoken YOUR_TOKEN_HERE
```

## 3. Start the bridge

In a terminal:

```bash
cd /path/to/hubspot-xero-bridge
python3 run.py
```

Leave it running. Default URL: `http://127.0.0.1:8080`.

## 4. Start the tunnel

In a **second** terminal:

```bash
ngrok http 8080
```

Or double-click `start-ngrok.command` in this repo (same as `ngrok http 8080`).

ngrok prints a **Forwarding** line, for example:

`https://abc123.ngrok-free.app -> http://localhost:8080`

Copy the **`https://…`** host (no path). That is your **public origin** for this session.

**Note:** On the free tier, the subdomain changes each time you restart ngrok unless you use a [reserved domain](https://dashboard.ngrok.com/cloud-edge/domains) (paid). When it changes, update HubSpot and Xero settings below.

## 5. Point the HubSpot card at the tunnel

1. Edit `hubspot-ui-extension/src/app/cards/XeroBridgeInvoice.jsx`.
2. Set:

   `const BRIDGE_BASE_URL = "https://abc123.ngrok-free.app";`

   (use your actual Forwarding URL origin.)

3. From `hubspot-ui-extension/`:

   ```bash
   hs project upload
   ```

## 6. Xero OAuth redirect (if you use “Connect Xero” through the tunnel)

Xero only allows redirects that are **exactly** registered on your Xero app.

- If you open the bridge **via ngrok**, set in `.env`:

  `XERO_REDIRECT_URI=https://YOUR-NGROK-HOST/auth/xero/callback`

- In the [Xero developer portal](https://developer.xero.com/app/manage), add the **same** URL under **Redirect URIs**.

- Restart `python3 run.py` after changing `.env`.

You can keep **both** `http://localhost:8080/auth/xero/callback` and your ngrok callback registered in Xero if you sometimes use localhost and sometimes ngrok.

## 7. ngrok browser warning (free tier)

Visitors may see an ngrok interstitial page once per session; click **Visit Site** if it appears.

## Troubleshooting

| Problem | What to try |
| --- | --- |
| `connection refused` via ngrok | Bridge not running on 8080, or wrong port in `ngrok http PORT`. |
| HubSpot link still wrong | `BRIDGE_BASE_URL` must be **only** the origin (scheme + host), no `/` path; re-run `hs project upload`. |
| Xero OAuth fails after tunnel URL change | Update `XERO_REDIRECT_URI` and Xero app redirect list to match the new `https://…/auth/xero/callback`. |
