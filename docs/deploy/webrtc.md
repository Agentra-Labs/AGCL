# Deploy AGCL with WebRTC peer-to-peer streaming

Skip the HTTP roundtrip — let the browser open a DataChannel directly
to the AGCL node. AGCL ships only the signaling half (offer/answer +
ICE exchange); the DataChannel itself is browser-native.

> Source: [agcl/toolkit/webrtc.py](../../agcl/toolkit/webrtc.py)
> · reference: [docs/integrations/cloud/webrtc.md](../integrations/cloud/webrtc.md)

---

## What you're building

```
   Browser (GUI)                                    AGCL node
   ─────────────                                    ───────────
   1. createOffer()                                 ┌─────────────┐
   2. POST /node/toolkit/webrtc/offer  ───────────► │ signaling   │
   3. GET  /node/toolkit/webrtc/answer ◄─────────── │ store       │
   4. POST /node/toolkit/webrtc/ice    ──┬──────────┤ (in-memory) │
   5. GET  /node/toolkit/webrtc/ice    ◄─┘          └─────────────┘
   6. RTCPeerConnection.connect()
   7. dataChannel.send("user msg")  ◄═══════════ direct UDP ═══════►
   8. dataChannel.onmessage  ◄═══════════ no HTTP roundtrip ════════►
```

After step 6 the browser and node have a direct connection. STUN
helps them find each other through NATs; TURN is the fallback when
symmetric NAT or strict firewalls block direct flow.

## Prerequisites

| Need                                                                | How                                              |
|---------------------------------------------------------------------|--------------------------------------------------|
| AGCL node running with `AGCL_WEBRTC_ENABLED=1`                       | env var                                          |
| A STUN server (free public ones are fine for dev)                    | nothing to install                               |
| A TURN server (needed for ~10% of NAT setups)                        | Twilio / Cloudflare TURN, or your own            |
| The browser-side WebRTC client (your code, or aiortc for a Python peer) | n/a                                          |

---

## Step 1 — Enable signaling on AGCL

```bash
echo "AGCL_WEBRTC_ENABLED=1"                              >> .env
echo "AGCL_STUN_URL=stun:stun.l.google.com:19302"         >> .env

# Optional TURN (uncomment if you have credentials)
# echo "AGCL_TURN_URL=turn:turn.example.com:3478"          >> .env
# echo "AGCL_TURN_USERNAME=user"                           >> .env
# echo "AGCL_TURN_PASSWORD=pass"                           >> .env
```

Reload via Setup → Cloud providers → **Reload .env**, or restart the
node.

---

## Step 2 — Verify the signaling surface

```bash
KEY=…your bearer key…
curl -H "Authorization: Bearer $KEY" \
     http://localhost:9876/node/toolkit/webrtc/status
```

Expected:

```json
{
  "enabled": true,
  "ice_servers": [
    {"urls": ["stun:stun.l.google.com:19302"]}
    // ... TURN if configured
  ],
  "sessions": 0
}
```

If `enabled: false`, the env var didn't load. Check Setup → Reload .env.

---

## Step 3 — Pick or run a TURN server (optional, ~10% of users need it)

A pure STUN setup works for most home networks. If your users sit
behind symmetric NAT (carrier-grade NAT, corporate firewalls), you
need TURN. Three options:

### Option A — Twilio's hosted TURN (cheapest for dev)

```bash
# Get credentials from https://www.twilio.com/console/voice/network-traversal/tokens
echo "AGCL_TURN_URL=turn:global.turn.twilio.com:3478?transport=udp" >> .env
echo "AGCL_TURN_USERNAME=…"                                          >> .env
echo "AGCL_TURN_PASSWORD=…"                                          >> .env
```

### Option B — Cloudflare TURN (more reliable, low cost)

```bash
# https://developers.cloudflare.com/calls/turn/
# Cloudflare gives you short-lived credentials; rotate them periodically.
```

### Option C — Self-hosted coturn

```bash
# On a VM with a public IP
sudo apt install coturn
# Edit /etc/turnserver.conf:
#   listening-port=3478
#   external-ip=<your.public.ip>
#   user=agcl:strong-password
#   realm=agcl.example.com
sudo systemctl enable --now coturn
```

Then point AGCL:

```bash
echo "AGCL_TURN_URL=turn:turn.example.com:3478"  >> .env
echo "AGCL_TURN_USERNAME=agcl"                   >> .env
echo "AGCL_TURN_PASSWORD=strong-password"        >> .env
```

---

## Step 4 — Browser client

Minimal example — replace your existing chat fetch with a DataChannel
send:

```js
const KEY = "…your bearer key…";
const HOST = "http://localhost:9876";
const SESSION = "demo";

// 1. Get the ICE servers AGCL is configured with
const r = await fetch(`${HOST}/node/toolkit/webrtc/status`,
  { headers: { Authorization: `Bearer ${KEY}` } });
const { ice_servers } = await r.json();

const pc = new RTCPeerConnection({ iceServers: ice_servers });
const ch = pc.createDataChannel("agcl-chat");

ch.onopen     = () => ch.send("hello from the browser");
ch.onmessage  = (e) => console.log("from node:", e.data);

// 2. Offer
const offer = await pc.createOffer();
await pc.setLocalDescription(offer);

await fetch(`${HOST}/node/toolkit/webrtc/offer`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${KEY}` },
  body: JSON.stringify({ session_id: SESSION, sdp: pc.localDescription }),
});

// 3. Poll for answer
let answer = null;
while (!answer) {
  const resp = await fetch(`${HOST}/node/toolkit/webrtc/answer/${SESSION}`,
    { headers: { Authorization: `Bearer ${KEY}` } });
  const j = await resp.json();
  if (j.sdp) answer = j.sdp;
  else await new Promise(r => setTimeout(r, 500));
}
await pc.setRemoteDescription(answer);

// 4. Trickle ICE
pc.onicecandidate = (e) => {
  if (e.candidate) {
    fetch(`${HOST}/node/toolkit/webrtc/ice`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${KEY}` },
      body: JSON.stringify({ session_id: SESSION, side: "a", candidate: e.candidate }),
    });
  }
};
setInterval(async () => {
  const j = await (await fetch(`${HOST}/node/toolkit/webrtc/ice/${SESSION}/b`,
    { headers: { Authorization: `Bearer ${KEY}` } })).json();
  for (const c of (j.candidates || [])) await pc.addIceCandidate(c);
}, 1000);
```

The signaling endpoints AGCL exposes are exactly the ones called
above; the in-memory store is in [agcl/toolkit/webrtc.py](../../agcl/toolkit/webrtc.py).

---

## Step 5 — Node-side peer (the half AGCL doesn't ship)

AGCL doesn't include a full WebRTC daemon — the signaling helpers
are intentionally lightweight. To complete the loop, you wire a
Python peer using `aiortc`:

```bash
pip install aiortc
```

A minimal node-side peer (`agcl_webrtc_peer.py`):

```python
import asyncio, os, httpx
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer

KEY = os.environ["AGCL_NODE_AUTH"]
HOST = os.environ.get("AGCL_NODE_URL", "http://localhost:9876")
SESSION = "demo"
HEADERS = {"Authorization": f"Bearer {KEY}"}

async def main():
    async with httpx.AsyncClient(headers=HEADERS) as h:
        # Get ICE servers from AGCL
        status = (await h.get(f"{HOST}/node/toolkit/webrtc/status")).json()
        ice = [RTCIceServer(**s) for s in status.get("ice_servers", [])]
        pc = RTCPeerConnection(RTCConfiguration(iceServers=ice))

        @pc.on("datachannel")
        def on_datachannel(ch):
            @ch.on("message")
            def on_message(msg):
                # echo for demo — in production, route to call_tool(...)
                ch.send(f"echo: {msg}")

        # Wait for the browser's offer
        offer = None
        while not offer:
            j = (await h.get(f"{HOST}/node/toolkit/webrtc/offer/{SESSION}")).json()
            if j.get("sdp"): offer = j["sdp"]
            else: await asyncio.sleep(0.5)

        await pc.setRemoteDescription(RTCSessionDescription(**offer))
        answer = await pc.createAnswer(); await pc.setLocalDescription(answer)
        await h.post(f"{HOST}/node/toolkit/webrtc/answer",
                     json={"session_id": SESSION,
                           "sdp": {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}})

        # ICE trickle
        async def push_ice():
            async def on_ice(c):
                await h.post(f"{HOST}/node/toolkit/webrtc/ice",
                             json={"session_id": SESSION, "side": "b", "candidate": {
                                 "candidate": c.candidate, "sdpMid": c.sdpMid, "sdpMLineIndex": c.sdpMLineIndex
                             }})
            pc.on("icecandidate", lambda e: asyncio.ensure_future(on_ice(e.candidate)) if e.candidate else None)
        await push_ice()

        # Pull peer ICE
        while True:
            j = (await h.get(f"{HOST}/node/toolkit/webrtc/ice/{SESSION}/a")).json()
            for c in j.get("candidates", []):
                await pc.addIceCandidate(c)
            await asyncio.sleep(1)

asyncio.run(main())
```

Run it alongside the AGCL node. Open the browser side — they connect
P2P after a couple of seconds.

---

## Verify it works

| Check                                                                 | Expected                                                            |
|-----------------------------------------------------------------------|----------------------------------------------------------------------|
| `curl /node/toolkit/webrtc/status`                                     | `{enabled: true, ice_servers: [...], sessions: 0}`                   |
| Browser console after `pc.connectionState === "connected"`             | `"connected"`                                                        |
| `pc.iceTransport.state` after a few seconds                            | `"completed"` or `"connected"`                                       |
| In strict NATs: `pc.candidatePair?.local.type` is `"relay"`            | confirms TURN is being used                                          |
| Send → echo round-trip                                                 | < 50ms over the same LAN, < 200ms across continents                  |

---

## Common problems

| Symptom                                                | Fix                                                                                                       |
|--------------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| `connectionState: failed`                              | Both sides on symmetric NAT, no TURN. Add Twilio / Cloudflare TURN credentials.                            |
| Offer never gets an answer                             | Node-side peer isn't running. The signaling endpoints are just a relay; the peer is a separate process.    |
| Browser shows "no compatible ICE candidates"           | STUN is unreachable. Test with `nc -u stun.l.google.com 19302` — if it hangs, your network blocks UDP.     |
| Connection works on LAN, dies over WAN                 | Likely UDP being blocked. TURN can fall back to TCP/443 — set `?transport=tcp` in the TURN URL.            |
| Sessions accumulate in memory                          | Old sessions live until `gc()` is called. The signaling endpoints garbage-collect after 5 min of inactivity. |
| `pc.iceConnectionState` flips between `connected` and `disconnected` | Network is unstable; this is normal. ICE re-establishes automatically.                                     |

---

## Why use WebRTC instead of plain HTTP

| Reason                                | HTTP/SSE                  | WebRTC DataChannel                    |
|---------------------------------------|---------------------------|----------------------------------------|
| Latency for token streaming           | ~50–200ms per chunk       | ~5–20ms per chunk                       |
| Works through corporate firewalls     | yes (HTTP/443)            | needs TURN/443 fallback                |
| Server bandwidth (many subscribers)   | linear in subscribers     | independent — peers stream from each other |
| Setup complexity                      | trivial                   | non-trivial (signaling + peer + TURN)  |
| Mobile data savings                   | no                        | yes (P2P bypasses your server)         |

For most users, plain SSE is fine. WebRTC is worth it when you have
latency-sensitive workloads (real-time voice, live coding) or many
subscribers where server bandwidth matters.

---

## What to read next

- The simpler "share over a Cloudflare Worker" pattern: [cloudflare.md](cloudflare.md)
- The full reference: [docs/integrations/cloud/webrtc.md](../integrations/cloud/webrtc.md)
- Security implications of session-id guessability: [docs/security.md](../security.md#11-webrtc-signaling)
