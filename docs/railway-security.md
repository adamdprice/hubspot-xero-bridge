# Securing the bridge on Railway (or any public URL)

Without protection, anyone who guesses your Railway URL could load the UI and call your APIs using the server’s **Xero + HubSpot credentials** stored in environment variables.

## What we added

When **`BRIDGE_AUTH_TOKEN`** is set (long random secret, e.g. `openssl rand -hex 32`):

1. **Browser session** — Opening  
   `https://your-app.railway.app/?token=YOUR_SECRET&deal_id=123`  
   validates the token, sets an **HttpOnly session cookie**, and redirects to the same page **without** the token in the URL.

2. **APIs** — All `/api/*` routes require either:
   - that session cookie (after a successful visit with `?token=`), or  
   - `Authorization: Bearer YOUR_SECRET` (for scripts).

3. **Exempt** — `/health` (for Railway health checks) and `/auth/xero/*` (OAuth redirect) stay reachable without the bridge token.

4. **Local dev** — If **`BRIDGE_AUTH_TOKEN` is unset** (empty), **no gate** is applied (same as before).

## Railway variables

| Variable | Example | Notes |
|----------|---------|--------|
| `BRIDGE_AUTH_TOKEN` | 64-char hex string | Required for production |
| `BRIDGE_COOKIE_SECURE` | `true` | Railway uses HTTPS; cookie must be Secure |
| `BRIDGE_SESSION_SECRET` | optional | If unset, derived from `BRIDGE_AUTH_TOKEN` |

## HubSpot card

In `hubspot-ui-extension/src/app/cards/XeroBridgeInvoice.jsx` set:

- **`BRIDGE_BASE_URL`** — your Railway origin, e.g. `https://your-app.up.railway.app`
- **`BRIDGE_AUTH_TOKEN`** — **the same value** as `BRIDGE_AUTH_TOKEN` on Railway

Then run **`hs project upload`**.

Anyone who can read the HubSpot app source (e.g. developers with project access) can see the token. Treat it like a **shared team password**; rotate it periodically and update Railway + the extension together.

## Stronger options (not implemented here)

- **IP allowlisting** (Railway edge / Cloudflare) in front of the app  
- **OAuth / SSO** for users instead of a shared secret  
- **Private networking** only (no public URL) — then HubSpot cannot link to the bridge from the browser without a tunnel

## Xero redirect URI

On Railway, set **`XERO_REDIRECT_URI`** to  
`https://your-app.up.railway.app/auth/xero/callback`  
and register that exact URL in the Xero developer app.
