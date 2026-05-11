# Deploy AGCL with Docker

Run the AGCL node inside a container on your laptop, a VPS, or any
host with Docker installed. Five minutes from zero to a working node.

> Source / emitter: [agcl/toolkit/docker.py](../../agcl/toolkit/docker.py)
> · auth + login helper: [agcl/integrations_auth.py](../../agcl/integrations_auth.py)
> · reference: [docs/integrations/docker.md](../integrations/docker.md)

---

## What you're building

```
┌────────────────────────────────────────────────────────────────┐
│                            host machine                         │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  docker compose                                           │   │
│  │   ├─ agcl-node  (port 9876)  ← FastAPI + bearer auth       │   │
│  │   ├─ ollama     (optional — local LLM server)              │   │
│  │   ├─ litellm    (optional — gateway / routing)             │   │
│  │   └─ redis      (optional — distributed state)             │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
```

Pick the optionals you actually want — by default you just get the
`agcl-node` container.

## Prerequisites

| Need                                  | How to check                           |
|---------------------------------------|----------------------------------------|
| Docker Engine ≥ 20.10                 | `docker --version`                     |
| Docker Compose v2                     | `docker compose version`               |
| (Optional) NVIDIA Container Toolkit   | `docker run --rm --gpus all nvidia/cuda:12.3.0-base-ubuntu22.04 nvidia-smi` |
| An AGCL clone with `.env` filled in   | `ls .env` should exist                 |

If `.env` doesn't exist yet, see [web_socket/README.md → Step 3](../../web_socket/README.md).

---

## Quick path — let AGCL emit the files

AGCL ships a deploy-manifest emitter that writes a ready-to-use
`Dockerfile`, `docker-compose.yml`, and `.dockerignore` based on the
config currently loaded into the node.

### From the web console

1. Start the node: `python main.py node --port 9876 --cors "*"`
2. Open `index.html`, paste the bearer key, Connect.
3. Go to **Deploy** in the sidebar.
4. Toggle the optional services you want (GPU / Ollama / LiteLLM / Redis).
5. Click **Emit Docker**.
6. Click **Download .json** — the response contains the full file
   contents.

### From the CLI

```bash
python main.py toolkit emit-docker --gpu --with-ollama --with-litellm --with-redis
```

This writes everything to `deploy/` by default. The Dockerfile pins
Python 3.11, copies in the project, installs the dev extras, and
exposes port 9876.

---

## Manual path — every step

### 1. Build the image

If you already have an emitted `Dockerfile`:

```bash
docker build -t agcl-node:local .
```

CPU-only image: ~1.5 GB. GPU image (CUDA base): ~6 GB.

### 2. Run the container

```bash
docker run --rm -it \
    --name agcl-node \
    -p 9876:9876 \
    --env-file .env \
    -e AGCL_NODE_AUTH=$(openssl rand -hex 32) \
    -v "$(pwd)/.agent_state:/app/.agent_state" \
    -v "$(pwd)/models:/app/models" \
    agcl-node:local \
    python main.py node --bind 0.0.0.0 --port 9876 --cors "*"
```

Notes:

- `-p 9876:9876` exposes the bearer-auth-protected HTTP API.
- `--env-file .env` brings in your `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / etc.
- `-e AGCL_NODE_AUTH=…` pins a stable bearer key so the GUI doesn't
  have to re-paste on every restart.
- The two volume mounts persist sessions and downloaded models across
  container restarts.

### 3. Connect from the GUI

Same Step 6 of the [web console quickstart](../../web_socket/README.md#step-6--connect):

- Host: `http://localhost:9876` (or your VPS's public IP)
- Bearer: the value of `AGCL_NODE_AUTH` you exported

---

## Compose (the recommended way)

Use the emitted `docker-compose.yml` to start the whole stack:

```bash
docker compose up -d            # start in background
docker compose logs -f agcl     # tail node logs
docker compose ps               # show all running services
docker compose down             # stop and remove
```

The Compose file wires the optional services together with internal
DNS — `agcl-node` talks to `ollama` at `http://ollama:11434`, etc.,
without you having to manage IPs.

### GPU passthrough

The emitted Compose file already includes the GPU stanza when you
passed `--gpu`:

```yaml
services:
  agcl-node:
    runtime: nvidia
    environment:
      NVIDIA_VISIBLE_DEVICES: all
```

Verify it works:

```bash
docker compose exec agcl-node nvidia-smi
```

---

## Pushing the image to a private registry

If you're deploying to a remote machine, push the image to a registry
the target can pull from.

### Log in (from the GUI — easiest)

Setup → Integrations → Docker → enter `ghcr.io` (or any registry),
your username, and a personal access token / password. Click
**Login**. The credentials go through `docker login --password-stdin`
and the password lands in `~/.docker/config.json` — never in `.env`.

### Log in (from the CLI)

```bash
python main.py auth docker-login ghcr.io --username YOUR_GH_USER
# prompts for the token via stdin
```

### Tag + push

```bash
docker tag agcl-node:local ghcr.io/YOUR_USER/agcl-node:0.1.0
docker push ghcr.io/YOUR_USER/agcl-node:0.1.0
```

---

## Verify it works

```bash
# 1. Liveness probe (no auth)
curl http://localhost:9876/node/health
# → {"ok": true, "service": "agcl-node", ...}

# 2. Auth check (with the key)
curl -X POST http://localhost:9876/node/auth/verify \
     -H "Authorization: Bearer $AGCL_NODE_AUTH"
# → {"ok": true, ...}

# 3. Provider sanity (if you set keys in .env)
curl http://localhost:9876/node/setup/providers/status \
     -H "Authorization: Bearer $AGCL_NODE_AUTH"
# → {"providers": {"anthropic": {"ok": true, ...}, ...}}
```

If `/node/health` answers but `/node/auth/verify` returns 401, the
container is running with a different `AGCL_NODE_AUTH` than what you
think. Check `docker compose exec agcl-node env | grep AGCL_NODE_AUTH`.

---

## Common problems

| Symptom                                        | Fix                                                                                        |
|------------------------------------------------|--------------------------------------------------------------------------------------------|
| `permission denied` on `/var/run/docker.sock`  | `sudo usermod -aG docker $USER`, log out and back in.                                       |
| Container exits immediately                    | `docker logs agcl-node` — usually a missing env var. Make sure `--env-file .env` is set.    |
| Bearer key changes every restart               | Set `AGCL_NODE_AUTH` in `.env` or via `-e` on `docker run`.                                  |
| Models redownload every restart                | Add `-v "$(pwd)/models:/app/models"` so the cache persists.                                  |
| GPU not detected                               | Install nvidia-container-toolkit and verify with `docker run --rm --gpus all nvidia/cuda:12.3.0-base-ubuntu22.04 nvidia-smi`. |
| `dial tcp: lookup ollama: no such host`        | You used `docker run` instead of `docker compose up`. Either use Compose, or pass `--network host`. |
| CORS errors in the browser                      | Restart with `--cors "https://your-page-host"` instead of `"*"` for production.              |

---

## What to read next

- Want a Kubernetes deploy? → [k8s.md](k8s.md)
- Want public HTTPS without a VPS? → [gcp.md](gcp.md) (Cloud Run)
- Want to put a TLS proxy in front? → [docs/security.md](../security.md#2-transport-security)
