# WebRTC node transport

<p align="left">
  <img src="../../assets/webrtc.svg" width="22" alt="WebRTC" />&nbsp;
  <a href="https://img.shields.io/badge/transport-DataChannel-333333"><img alt="DataChannel" src="https://img.shields.io/badge/transport-DataChannel-333333"></a>
  <a href="https://img.shields.io/badge/encryption-DTLS-2E7D32"><img alt="DTLS" src="https://img.shields.io/badge/encryption-DTLS-2E7D32"></a>
  <a href="https://img.shields.io/badge/status-experimental-yellow"><img alt="Status" src="https://img.shields.io/badge/status-experimental-yellow"></a>
</p>

**What it is:** Peer-to-peer, encrypted, low-latency transport between
AGCL GUI and node — or between nodes. Eliminates the GUI → HTTP → Node
relay hop. Supports NAT traversal via STUN / TURN.

**Status:** Long-term / advanced feature. Recommended to prototype
with [aiortc](https://github.com/aiortc/aiortc) (Python) or
[node-webrtc](https://github.com/node-webrtc/node-webrtc).

> Part of the **[Cloud / infra integrations](../cloud.md)**.

---

## Architecture comparison

```
Current:    GUI  ──HTTP/SSE──►  AGCL Node
WebRTC:     GUI  ◄──DataChannel──►  AGCL Node
                        ▲
              STUN/TURN for NAT traversal
              DTLS encryption (built-in)
              Direct streaming, sub-100ms latency
```

---

## Python (aiortc)

```bash
pip install aiortc aiohttp
```

```python
import json
from aiortc import RTCPeerConnection, RTCSessionDescription

class AgclWebRTCNode:
    def __init__(self):
        self.pc = RTCPeerConnection()
        self.channel = None

    async def create_offer(self):
        self.channel = self.pc.createDataChannel("agcl")
        self.channel.on("message", self.on_message)

        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        return self.pc.localDescription

    async def on_message(self, message):
        # message is a JSON-encoded AGCL task
        task = json.loads(message)
        result = await agcl_run(task["prompt"])
        self.channel.send(json.dumps({"result": result}))
```

---

## Signaling options

| Approach | Notes |
|---|---|
| WebSocket signaling server | Simple; AGCL already has WS infra |
| Cloudflare Durable Objects | Edge signaling — pairs perfectly with [cloudflare.md](cloudflare.md) |
| STUN server | `stun:stun.l.google.com:19302` (free, use for dev) |
| TURN server | Needed for strict NAT; `coturn` self-hosted or Twilio / Cloudflare TURN |

---

## Env vars (when implemented)

```env
AGCL_WEBRTC_ENABLED=true
AGCL_STUN_URL=stun:stun.l.google.com:19302
AGCL_TURN_URL=turn:turn.example.com:3478
AGCL_TURN_USERNAME=agcl
AGCL_TURN_PASSWORD=secret
AGCL_SIGNAL_URL=wss://agcl-signal.example.com/ws
```
