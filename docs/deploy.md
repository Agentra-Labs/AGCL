# Deploying AGCL — start here

This is the practical, "I just want to deploy this" index. Each link
below is a step-by-step recipe with the exact commands you need.
The longer reference material (architecture, configuration knobs,
threat model) lives in the sister docs and is linked from each recipe
where it's relevant.

> If this is your **first time touching AGCL at all**, start with
> [web_socket/README.md](../web_socket/README.md) to get a working
> chat on your laptop. Come back here when you want to deploy.

---

## Pick your goal

| You want to…                                                  | Read                                                  |
|---------------------------------------------------------------|--------------------------------------------------------|
| Run AGCL in a container on one machine                        | [deploy/docker.md](deploy/docker.md)                  |
| Run AGCL on a Kubernetes cluster (kind, k3s, EKS, GKE, AKS)   | [deploy/k8s.md](deploy/k8s.md)                        |
| Deploy AGCL to Google Cloud Run (scale-to-zero HTTPS)         | [deploy/gcp.md](deploy/gcp.md)                        |
| Expose AGCL over the public internet through Cloudflare       | [deploy/cloudflare.md](deploy/cloudflare.md)          |
| Run AGCL as a Discord bot                                     | [deploy/discord.md](deploy/discord.md)                |
| Put AGCL behind a LiteLLM proxy (routing, fallbacks, virtual keys) | [deploy/litellm_gw.md](deploy/litellm_gw.md)     |
| Use a self-hosted vLLM server as the cloud model              | [deploy/vllm.md](deploy/vllm.md)                      |
| Use HuggingFace Text-Generation-Inference (TGI)               | [deploy/tgi.md](deploy/tgi.md)                        |
| Use Ollama as the cloud model                                 | [deploy/ollama.md](deploy/ollama.md)                  |
| Plug in any OpenAI-compatible provider (Together, Groq, Fireworks, DeepSeek…) | [deploy/openai_compat.md](deploy/openai_compat.md) |
| Share state across multiple AGCL nodes (Redis / Valkey)       | [deploy/redis_state.md](deploy/redis_state.md)        |
| Move trained-topic checkpoints to S3 / R2 / MinIO / B2        | [deploy/s3_store.md](deploy/s3_store.md)              |
| Set up peer-to-peer GUI ↔ node via WebRTC + STUN/TURN         | [deploy/webrtc.md](deploy/webrtc.md)                  |

---

## How these recipes are organized

Every recipe follows the same shape so you always know where to look:

1. **What you're building** — one-paragraph "what runs where".
2. **Prerequisites** — what you need installed / what accounts.
3. **Step-by-step** — every command, in order, with what to expect.
4. **Wire it into AGCL** — the exact env vars / config keys.
5. **Verify it works** — a small test that exercises the path.
6. **Common problems** — fixes for the things that usually break.

Where AGCL ships a CLI or web-console helper that automates a step,
the recipe says so and shows the equivalent button or command. For
example, **Setup → Integrations** in the web console can do `docker
login`, save GCP service-account credentials, and set up a kubeconfig
without you typing anything in the terminal —
see [deploy/docker.md](deploy/docker.md) §"Quick path".

---

## Stack picker

If you don't already know which combination you want:

### "I just want to run AGCL somewhere"

- **One machine, you don't care about scaling** →
  [deploy/docker.md](deploy/docker.md)
- **One machine, no Docker** → just `python main.py node` — that's it,
  no recipe needed.
- **Public HTTPS, scale-to-zero, almost-free** →
  [deploy/gcp.md](deploy/gcp.md) (Cloud Run)
- **Anything bigger** → [deploy/k8s.md](deploy/k8s.md)

### "I want to use AGCL with a cloud model"

- **Direct to OpenAI / Anthropic / DeepSeek** — already supported,
  just add the API key. See [web-console.md](web-console.md) Setup tab.
- **Together / Fireworks / Groq / any OpenAI-compatible** →
  [deploy/openai_compat.md](deploy/openai_compat.md)
- **Multiple providers, with routing + fallbacks** →
  [deploy/litellm_gw.md](deploy/litellm_gw.md)

### "I want to run my own model"

- **Smallest setup, GGUF on CPU/GPU** — already built in (local-prefix
  model), see Setup tab → "GGUF file downloader".
- **A few users, single GPU** → [deploy/ollama.md](deploy/ollama.md)
- **Many users, throughput-focused** → [deploy/vllm.md](deploy/vllm.md)
- **HF ecosystem, legacy deployment** → [deploy/tgi.md](deploy/tgi.md)

### "I want state to survive restarts / scale horizontally"

- **One node** — default local FS is fine.
- **Multiple nodes** → [deploy/redis_state.md](deploy/redis_state.md)
  for session state + [deploy/s3_store.md](deploy/s3_store.md) for
  trained-topic checkpoints.

### "I want a chat interface that isn't the web console"

- **Discord** → [deploy/discord.md](deploy/discord.md)
- **Slack** → [docs/integrations/slack.md](integrations/slack.md)
- **Browser, but P2P** → [deploy/webrtc.md](deploy/webrtc.md)

---

## What the web console can do for you

Most of the cred-storage steps in these recipes are also available as
one-click forms in the Setup tab of the bundled console:

| Recipe step                              | GUI equivalent                                              |
|------------------------------------------|--------------------------------------------------------------|
| `docker login <registry>`                | Setup → Integrations → Docker → login                       |
| Save `GOOGLE_APPLICATION_CREDENTIALS`    | Setup → Integrations → Google Cloud → Connect               |
| Save `KUBECONFIG`                        | Setup → Integrations → Kubernetes → Connect                 |
| Provider key status + quota / balance    | Setup → Cloud providers → Check now                         |
| Reload `.env` without restarting node    | Setup → Cloud providers → Reload .env                        |
| Download a HF snapshot                   | Setup → HuggingFace snapshot downloader                      |
| Download a single `.gguf` file           | Setup → GGUF file downloader                                |
| Emit Docker / Kubernetes / GCP manifests | Deploy tab                                                  |

Anything that can be a button in the GUI **is** a button in the GUI.
Anything that has to touch a real CLI tool (running `docker login`,
running `kubectl cluster-info`) is wrapped — the GUI shells out so
you don't have to.

---

## Reference docs (for when you want depth, not steps)

- Existing per-integration reference: [docs/integrations/](integrations/) and [docs/integrations/cloud/](integrations/cloud/)
- Every config knob explained: [docs/configuration.md](configuration.md)
- The HTTP API the GUI talks to: [docs/gui/endpoints.md](gui/endpoints.md)
- Security posture / threat model: [docs/security.md](security.md)
- Troubleshooting tree: [docs/troubleshooting.md](troubleshooting.md)

The recipes here intentionally don't duplicate that content — they
link to it where you need the long version.
