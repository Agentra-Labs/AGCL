# Going from local-only to remote nodes

Today the node defaults to `--bind 127.0.0.1` (loopback only). For
Phase 2 (remote nodes connecting to a central server), the user runs:

```bash
python main.py node --bind 0.0.0.0 --port 9876 --auth-key $STABLE_KEY
```

Then the central server connects to `http://<user-public-ip>:9876` with
the shared key. The endpoint surface is identical — that's the whole
point of this module.

> Part of the **[GUI integration guide](../gui.md)**.
> See also: [Connect](connect.md) for the local pairing flow.

---

## Production hardening checklist

- **TLS** — terminate with caddy / nginx / cloudflared in front of the
  node. The node speaks plain HTTP; let your reverse proxy handle
  certs and HSTS.
- **JWT-style auth** — this v1 uses a simple opaque bearer. For
  multi-tenant or expiry semantics, layer JWT validation in the
  reverse proxy or a sidecar.
- **Per-route rate limiting** — the existing `pressure.py` tracks but
  doesn't enforce; add a 429 in `/mas/run` if the user wants it.
- **CORS lockdown** — start the node with
  `--cors https://your-gui.example.com` instead of the `*` default.

For an in-development GUI on the same machine, the default setup is
enough.

---

## Containerized / cloud deployment

If you want to run the node as a long-lived service inside Docker,
Compose, or Kubernetes — including with managed Redis, S3-backed
checkpoints, or a LiteLLM gateway in front of inference providers —
see the **[Cloud integrations](../integrations/cloud.md)** guides:

- [Docker / docker-compose](../integrations/docker.md)
- [Kubernetes + Helm](../integrations/cloud/k8s.md)
- [Redis / Valkey for distributed session state](../integrations/cloud/redis.md)
- [S3-compatible persistence](../integrations/cloud/s3.md)
- [Cloudflare Workers edge relay](../integrations/cloud/cloudflare.md)

---

## Tunnels for "remote without a public IP"

If the user can't open a port (residential ISP, corporate NAT), the
node can still be reached via a tunnel. Two options:

| Tool | Command | Notes |
|---|---|---|
| **cloudflared** | `cloudflared tunnel --url http://localhost:9876` | Adds TLS, gives a `*.trycloudflare.com` URL |
| **ngrok** | `ngrok http 9876` | Same idea; auth on free tier |

Both surface a public HTTPS URL the GUI can paste into the host field —
the bearer auth still applies on top.
