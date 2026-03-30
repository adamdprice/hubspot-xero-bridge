import React, { useMemo } from "react";
import { Flex, Text, Link, hubspot, useExtensionApi } from "@hubspot/ui-extensions";

/**
 * REQUIRED: public HTTPS origin of your FastAPI bridge (same host you use in the browser).
 * Example: "https://abc123.ngrok-free.app" — replace with your real URL, then run `hs project upload`.
 * Free ngrok URLs change when you restart the tunnel — update this and re-upload if the host changes.
 */
const BRIDGE_BASE_URL = "https://web-production-02635.up.railway.app";

/** Must match Railway (or host) env BRIDGE_AUTH_TOKEN when gate is enabled. Leave "" for local dev with no token. */
const BRIDGE_AUTH_TOKEN = "";

/** Only the template default counts as “not set” — avoid substring checks (e.g. “example”) on real ngrok hosts. */
const BRIDGE_URL_PLACEHOLDER = "https://your-bridge.example.com";

function bridgeBaseIsConfigured() {
  const b = String(BRIDGE_BASE_URL).trim().toLowerCase();
  if (!b.startsWith("https://")) {
    return false;
  }
  return b !== BRIDGE_URL_PLACEHOLDER;
}

hubspot.extend(() => <Extension />);

function Extension() {
  const { context } = useExtensionApi();
  const dealId = context?.crm?.objectId;

  const bridgeUrl = useMemo(() => {
    if (dealId == null || dealId === "") {
      return null;
    }
    const base = String(BRIDGE_BASE_URL).replace(/\/$/, "");
    const params = new URLSearchParams();
    params.set("deal_id", String(dealId));
    const tok = String(BRIDGE_AUTH_TOKEN || "").trim();
    if (tok) {
      params.set("token", tok);
    }
    return `${base}/?${params.toString()}`;
  }, [dealId]);

  if (!bridgeUrl) {
    return (
      <Flex direction="column" gap="small">
        <Text>
          Could not read this deal ID. Open a deal record (not a list or board) and try again.
        </Text>
      </Flex>
    );
  }

  const configured = bridgeBaseIsConfigured();

  return (
    <Flex direction="column" gap="medium">
      <Text>
        Open the bridge in a new tab to search or confirm billing details and create a draft Xero invoice for
        deal{" "}
        <Text inline={true} format={{ fontWeight: "bold" }}>
          {String(dealId)}
        </Text>
        .
      </Text>
      {configured ? (
        <Link href={{ url: bridgeUrl, external: true }}>Open Xero invoice bridge</Link>
      ) : (
        <Flex direction="column" gap="small">
          <Text format={{ fontWeight: "bold" }}>Bridge URL not set in this app yet</Text>
          <Text variant="microcopy">
            HubSpot cannot open your local bridge until you put its public HTTPS URL in the extension source.
            In this repo, edit hubspot-ui-extension/src/app/cards/XeroBridgeInvoice.jsx — set BRIDGE_BASE_URL to
            the same origin you use when you open the bridge (for example your ngrok URL like
            https://yoursubdomain.ngrok-free.app). Then run hs project upload from hubspot-ui-extension/.
          </Text>
        </Flex>
      )}
      <Text variant="microcopy">
        The bridge runs outside HubSpot; keep it on HTTPS and ensure your Xero OAuth and HubSpot token are
        configured on that server.
      </Text>
    </Flex>
  );
}
