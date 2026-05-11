# Deploy Ollama as AGCL's local model server

Ollama is the easiest way to run a model locally — one install, one
`ollama pull`, and you have an OpenAI-compatible endpoint. Best for
laptops, personal servers, and "I just want to try a local model".

> Source: [agcl/toolkit/ollama.py](../../agcl/toolkit/ollama.py)
> · reference: [docs/integrations/cloud/ollama.md](../integrations/cloud/ollama.md)

---

## What you're building

```
   AGCL node
   ─────────
       │   POST http://localhost:11434/v1/chat/completions
       ▼
   ┌──────────────────────────────────────┐
   │  Ollama server                         │
   │   ─ /v1/chat/completions  (OpenAI)     │
   │   ─ /v1/models                          │
   │   ─ /api/generate         (native)     │
   │   ─ /api/tags, /api/show, /api/pull    │
   └──────────────────────────────────────┘
```

## Prerequisites

| Need                                                | How                                                  |
|-----------------------------------------------------|------------------------------------------------------|
| Linux / macOS / Windows                             | https://ollama.com/download                          |
| 8 GB RAM for small models, 16+ for medium           | `free -h`                                            |
| (Optional) GPU                                       | NVIDIA / Apple Silicon / AMD — Ollama auto-detects   |

---

## Step 1 — Install Ollama

```bash
# Linux:
curl -fsSL https://ollama.com/install.sh | sh

# macOS:
brew install ollama
# or download from ollama.com

# Windows:
# download from ollama.com
```

The installer also starts the daemon as a system service on most
platforms. Verify:

```bash
ollama --version
curl http://localhost:11434/api/tags
# → {"models": [...]}  (empty list on a fresh install)
```

---

## Step 2 — Pull a model

```bash
ollama pull llama3.1:8b              # ~5 GB, runs on 8 GB GPU / 16 GB RAM
# or smaller / bigger:
ollama pull llama3.2:1b              # ~1.5 GB, runs anywhere
ollama pull qwen2.5:7b                # ~5 GB
ollama pull gemma2:9b                 # ~5 GB
ollama pull phi3.5                    # ~2 GB
```

Pull progress prints to your terminal. Once done:

```bash
ollama list
# NAME             ID       SIZE     MODIFIED
# llama3.1:8b      xxx      5.0 GB   2 minutes ago
```

---

## Step 3 — Smoke-test

```bash
# Native API
curl http://localhost:11434/api/generate -d '{
  "model": "llama3.1:8b",
  "prompt": "Say hi in five words.",
  "stream": false
}'

# OpenAI-compatible
curl http://localhost:11434/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{
       "model": "llama3.1:8b",
       "messages": [{"role": "user", "content": "Say hi in five words."}]
     }'
```

Both should return a response. AGCL uses the OpenAI-compatible path.

---

## Step 4 — Wire AGCL to Ollama

```bash
echo "OLLAMA_HOST=http://localhost:11434"   >> .env
echo "OLLAMA_MODEL=llama3.1:8b"             >> .env
```

Or register as a custom provider (Setup → Custom OpenAI-compatible
providers):

| Field          | Value                                      |
|----------------|--------------------------------------------|
| name           | `ollama-local`                            |
| base_url       | `http://localhost:11434/v1`               |
| api_key_env    | any var; Ollama doesn't enforce auth      |
| model          | `llama3.1:8b`                              |

Verify:

```bash
python main.py toolkit ping ollama
# → {"ok": true, "status": 200, "url": "http://localhost:11434/v1"}
```

---

## Step 5 — Manage models from AGCL

The toolkit module exposes model-management helpers that map to
Ollama's native API (not the OpenAI-compat one):

```python
import asyncio
from agcl.toolkit.ollama import list_local_models, show_model, pull_model

asyncio.run(list_local_models())
# [{"name": "llama3.1:8b", "size": 5000000000, ...}]

asyncio.run(show_model("llama3.1:8b"))
# {"license": "...", "modelfile": "...", "parameters": "...", ...}

# Pull yields progress dicts as the download proceeds
async def go():
    async for chunk in pull_model("qwen2.5:7b"):
        print(chunk)
asyncio.run(go())
```

There's currently no GUI button for this — you can still trigger
pulls from a terminal alongside the running node.

---

## Step 6 — Verify end-to-end

In the GUI **Chat** tab:

1. Provider dropdown — pick `ollama-local` (if registered) or leave
   blank if `OLLAMA_HOST` is in `.env` (then it's the routing default).
2. Send a message.
3. The local prefix bubble + assistant bubble both flow; the cloud
   reply now comes from Ollama, not OpenAI/Anthropic.

The terminal running Ollama will log each request:

```
[GIN] | 200 | 1.234s | "POST /v1/chat/completions"
```

---

## Custom models (Modelfile)

You can tweak any model — system prompt, temperature, stop tokens:

```bash
cat > Modelfile <<'EOF'
FROM llama3.1:8b
SYSTEM You are an AGCL agent. Be concise.
PARAMETER temperature 0.6
PARAMETER num_ctx 8192
EOF

ollama create agcl-llama -f Modelfile
ollama list   # confirms 'agcl-llama' is now selectable
```

Update `.env` to point at the custom model:

```bash
OLLAMA_MODEL=agcl-llama
```

---

## GPU + multi-host

### GPU detection

Ollama auto-detects:

```bash
ollama serve & sleep 2; ollama run llama3.1:8b --verbose <<< "hi" 2>&1 | head
# → "ggml_cuda_init: found 1 CUDA devices: ..."  (or "Metal" / "ROCm")
```

If your GPU isn't picked up, check that `nvidia-smi` works and that
the Ollama service is running with access to the GPU. On Linux,
re-running the install script will detect a freshly installed CUDA.

### Networked Ollama

By default Ollama binds to `127.0.0.1`. To make it reachable from
another machine (e.g., AGCL running on a different node):

```bash
# systemd (Linux):
sudo systemctl edit ollama
# add:
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl restart ollama

# macOS:
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
# then quit + restart Ollama
```

In AGCL's `.env`:

```bash
OLLAMA_HOST=http://other-host.local:11434
```

Then put Ollama behind a real reverse-proxy with TLS + auth if you
expose it beyond your LAN.

---

## Common problems

| Symptom                                                | Fix                                                                                              |
|--------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| `curl /api/tags` → "connection refused"                | Daemon not running. `ollama serve` (or restart the system service).                              |
| `model 'X' not found` from AGCL                        | You didn't `ollama pull X`. Confirm `ollama list` includes it.                                    |
| Streaming returns whole response at once               | Some clients/SDKs default to non-streaming. AGCL streams when you pass `stream=true`.             |
| Out of memory                                          | Pull a smaller variant: `llama3.1:8b` → `llama3.2:3b` or `llama3.2:1b`.                           |
| GPU not used                                            | Check `ollama ps` — should show "GPU 100%". If not, drivers / Toolkit not set up.                  |
| Sluggish first request                                 | Model is being loaded into GPU memory. Keep-alive defaults to 5 min; subsequent requests are fast. |
| Browser CORS error to Ollama directly                  | Ollama doesn't set CORS. Route through AGCL or add a reverse proxy.                                |

---

## Operating notes

- **Memory pressure**: Ollama keeps the model in VRAM for
  `OLLAMA_KEEP_ALIVE` (default `5m`). Set `0` to unload immediately
  after each request, or `24h` to keep it warm.
- **Model storage**: ~/.ollama/models (Linux/macOS) or
  C:\Users\<you>\.ollama\models (Windows). Move with the
  `OLLAMA_MODELS` env var.
- **Switching models per session**: AGCL respects the provider
  dropdown in the Chat tab — register multiple Ollama providers each
  pointing at a different `model` to give yourself a switcher.

---

## What to read next

- For heavier production loads: [vllm.md](vllm.md)
- Put Ollama behind LiteLLM for fallback to a cloud provider: [litellm_gw.md](litellm_gw.md)
- Full reference: [docs/integrations/cloud/ollama.md](../integrations/cloud/ollama.md)
