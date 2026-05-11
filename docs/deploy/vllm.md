# Deploy vLLM as AGCL's local model server

vLLM is the throughput champion for self-hosted LLM inference —
PagedAttention, continuous batching, speculative decoding, native
multi-GPU. Use it when you need fast, parallel inference for a
heavier model than llama.cpp can handle.

> Source: [agcl/toolkit/vllm.py](../../agcl/toolkit/vllm.py)
> · reference: [docs/integrations/cloud/vllm.md](../integrations/cloud/vllm.md)

---

## What you're building

```
   AGCL node
   ─────────
       │   POST http://vllm:8000/v1/chat/completions
       ▼
   ┌────────────────────────────────┐
   │  vLLM OpenAI-compatible server  │
   │    ─ multi-GPU sharding         │
   │    ─ continuous batching        │
   │    ─ /v1/chat/completions       │
   │    ─ /v1/models                 │
   │    ─ /metrics  (Prometheus)     │
   └────────────────────────────────┘
```

## Prerequisites

| Need                                                | Check                                          |
|-----------------------------------------------------|-------------------------------------------------|
| An NVIDIA GPU with ≥ 24 GB VRAM (less for small models) | `nvidia-smi`                                  |
| CUDA 12.1+ drivers                                  | `nvidia-smi | grep "CUDA Version"`              |
| Docker with NVIDIA Container Toolkit                | `docker run --rm --gpus all nvidia/cuda:12.3.0-base nvidia-smi` |
| A HuggingFace account + token (for gated models)    | `HUGGING_FACE_HUB_TOKEN`                       |

Approx memory needs: model size in fp16 ≈ params × 2 GB. Llama-3.1-8B
needs ~16 GB; 70B needs ~140 GB (4× A100/H100 80GB).

---

## Step 1 — Pick a model

For testing, pick something small and ungated:

```
Qwen/Qwen2.5-7B-Instruct                       — 14 GB fp16, runs on RTX 4090/3090
meta-llama/Meta-Llama-3.1-8B-Instruct          — 16 GB fp16, gated (request access)
mistralai/Mistral-7B-Instruct-v0.3             — 14 GB fp16
microsoft/Phi-3.5-mini-instruct                — 8 GB fp16, runs on RTX 4080
```

For production / heavier reasoning, try Llama-3.1-70B (needs 4× 80GB
or 8× 40GB) — vLLM handles the sharding automatically.

---

## Step 2 — Run vLLM (Docker, simplest)

```bash
# Single-GPU
docker run -d --name vllm \
    --runtime nvidia --gpus all \
    -p 8000:8000 \
    -v ~/.cache/huggingface:/root/.cache/huggingface \
    -e HUGGING_FACE_HUB_TOKEN=$HF_TOKEN \
    --ipc=host \
    vllm/vllm-openai:latest \
    --model Qwen/Qwen2.5-7B-Instruct \
    --port 8000 \
    --max-model-len 16384

# Multi-GPU (tensor parallel)
docker run -d --name vllm \
    --runtime nvidia --gpus all \
    -p 8000:8000 \
    -v ~/.cache/huggingface:/root/.cache/huggingface \
    -e HUGGING_FACE_HUB_TOKEN=$HF_TOKEN \
    --ipc=host \
    --shm-size=16g \
    vllm/vllm-openai:latest \
    --model meta-llama/Meta-Llama-3.1-70B-Instruct \
    --tensor-parallel-size 4 \
    --port 8000
```

First start is slow — vLLM has to download the model and build CUDA
graphs. Watch `docker logs -f vllm` for `Uvicorn running on
http://0.0.0.0:8000`.

---

## Step 3 — Smoke-test the server

```bash
# List models (auth header optional unless you set --api-key)
curl http://localhost:8000/v1/models

# Chat
curl http://localhost:8000/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{
       "model": "Qwen/Qwen2.5-7B-Instruct",
       "messages": [{"role": "user", "content": "Say hi in five words."}]
     }'
```

`/health` should return 200 once the model is loaded. `/metrics` is
the Prometheus endpoint.

---

## Step 4 — Wire AGCL to vLLM

Three options for routing. Pick one.

### Option A — vLLM is THE cloud (one provider only)

```bash
echo "VLLM_HOST=http://localhost:8000"          >> .env
echo "VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct"      >> .env
echo "VLLM_API_KEY=EMPTY"                       >> .env   # vLLM defaults to no auth
```

The toolkit routing in [agcl/toolkit/registry.py](../../agcl/toolkit/registry.py)
will pick vLLM over OpenAI/Anthropic when `VLLM_HOST` is set and
`AGCL_LLM_BASE_URL` is not.

### Option B — Register as a custom provider

For when you want vLLM **plus** Claude/OpenAI selectable per session:

GUI → Setup → Custom OpenAI-compatible providers:

| Field          | Value                                          |
|----------------|------------------------------------------------|
| name           | `vllm-local`                                  |
| base_url       | `http://localhost:8000/v1`                    |
| api_key_env    | `VLLM_API_KEY` (set to `EMPTY` if no auth)    |
| model          | `Qwen/Qwen2.5-7B-Instruct`                    |
| kind           | `chat`                                        |

Now Chat tab → provider dropdown shows `vllm-local`.

### Option C — Behind LiteLLM

Add vLLM as one entry in your LiteLLM `model_list`. See
[litellm_gw.md](litellm_gw.md) → "Add more providers".

---

## Step 5 — Verify

```bash
python main.py toolkit ping vllm
# → {"ok": true, "status": 200, "url": "http://localhost:8000/v1"}

python main.py toolkit chat "Hello in five words." --stream
# streams tokens through vLLM
```

In the GUI: Toolkit tab → ping `vllm` → toast `{ok: true, ...}`.

---

## Production tweaks

### Bound max-model-len

vLLM's default is the model's max context, which may exceed VRAM.
Set explicitly:

```bash
--max-model-len 16384   # 16k context
```

Symptom of "too big": OOM at startup or random "out of memory" mid-request.

### Enable an API key

Force callers to authenticate to vLLM itself (orthogonal to AGCL's
bearer):

```bash
docker run … vllm/vllm-openai:latest \
    --model Qwen/Qwen2.5-7B-Instruct \
    --api-key sk-vllm-$(openssl rand -hex 16)
```

Update `.env`:

```bash
VLLM_API_KEY=sk-vllm-…the key you set…
```

### Speculative decoding

For ~1.5–2× throughput on certain models:

```bash
--speculative-model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \
--num-speculative-tokens 5
```

### Pinning to specific GPUs

```bash
docker run --gpus '"device=0,1"' …
# or:
-e CUDA_VISIBLE_DEVICES=0,1
```

### Persistence + restart

```bash
docker run -d --name vllm \
    --restart unless-stopped \
    --runtime nvidia --gpus all \
    -p 8000:8000 \
    -v ~/.cache/huggingface:/root/.cache/huggingface \
    -e HUGGING_FACE_HUB_TOKEN=$HF_TOKEN \
    --ipc=host \
    vllm/vllm-openai:latest \
    --model Qwen/Qwen2.5-7B-Instruct
```

`--restart unless-stopped` brings vLLM back automatically on reboot.

---

## Kubernetes deployment

Sketch — see the full pattern in [docs/integrations/cloud/vllm.md](../integrations/cloud/vllm.md):

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: vllm, namespace: agcl }
spec:
  replicas: 1
  selector: { matchLabels: { app: vllm } }
  template:
    metadata: { labels: { app: vllm } }
    spec:
      containers:
        - name: vllm
          image: vllm/vllm-openai:latest
          args:
            - "--model"
            - "Qwen/Qwen2.5-7B-Instruct"
            - "--max-model-len"
            - "16384"
          ports: [{ containerPort: 8000 }]
          resources:
            limits:
              nvidia.com/gpu: 1
          volumeMounts:
            - { name: hf-cache, mountPath: /root/.cache/huggingface }
          env:
            - name: HUGGING_FACE_HUB_TOKEN
              valueFrom: { secretKeyRef: { name: hf-secret, key: token } }
      volumes:
        - name: hf-cache
          persistentVolumeClaim: { claimName: vllm-cache }
---
apiVersion: v1
kind: Service
metadata: { name: vllm, namespace: agcl }
spec:
  selector: { app: vllm }
  ports: [{ port: 8000, targetPort: 8000 }]
```

Then in AGCL's secret: `VLLM_HOST=http://vllm:8000`.

---

## Common problems

| Symptom                                                | Fix                                                                                                  |
|--------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| `RuntimeError: CUDA out of memory` at startup          | Lower `--max-model-len`, switch to smaller model, or add a GPU and use `--tensor-parallel-size 2`.    |
| Hangs at "Loading model" forever                       | First download — watch network. Once cached, subsequent boots are fast.                              |
| `403 Forbidden` from HuggingFace                       | Model is gated. Request access on the HF model page; set `HUGGING_FACE_HUB_TOKEN`.                    |
| AGCL toolkit ping returns `unreachable`                | vLLM not bound to `0.0.0.0`, or you're in Docker without host networking. Check `--host 0.0.0.0`.    |
| Slow first request                                     | vLLM's CUDA graph compilation. Subsequent requests are fast.                                          |
| Garbled output                                          | Model name mismatch — server loaded model X but you asked for Y. Check `/v1/models`.                  |
| OOM mid-request with many concurrent users             | `--max-num-seqs` is too high for your VRAM. Lower it (default 256).                                   |

---

## What to read next

- TGI alternative (HF's older engine, similar API): [tgi.md](tgi.md)
- Lighter alternative for personal use: [ollama.md](ollama.md)
- Put vLLM behind LiteLLM for routing + fallback: [litellm_gw.md](litellm_gw.md)
- Full reference (config flags, observability, autoscaling): [docs/integrations/cloud/vllm.md](../integrations/cloud/vllm.md)
