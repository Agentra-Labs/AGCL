# Deploy HuggingFace Text-Generation-Inference (TGI)

TGI is HuggingFace's original serving engine — production-grade, with
its own legacy `/generate` API and an OpenAI-compatible
`/v1/chat/completions` route. **TGI entered maintenance mode in late
2025**; new deployments should use [vllm.md](vllm.md). Keep this for
compatibility with existing clusters.

> Source: [agcl/toolkit/tgi.py](../../agcl/toolkit/tgi.py)
> · reference: [docs/integrations/cloud/tgi.md](../integrations/cloud/tgi.md)

---

## What you're building

```
   AGCL node
   ─────────
       │   POST http://tgi:8080/v1/chat/completions
       ▼
   ┌──────────────────────────────────────┐
   │  TGI server                            │
   │   ─ /v1/chat/completions (OpenAI compat)│
   │   ─ /v1/models                          │
   │   ─ /generate (legacy native)            │
   │   ─ /info                                │
   │   ─ /metrics (Prometheus)                │
   └──────────────────────────────────────┘
```

## Prerequisites

| Need                                              | Check                                                  |
|---------------------------------------------------|--------------------------------------------------------|
| NVIDIA GPU with ≥ 16 GB VRAM                      | `nvidia-smi`                                           |
| Docker with NVIDIA Container Toolkit              | `docker run --rm --gpus all nvidia/cuda:12.3.0-base nvidia-smi` |
| HuggingFace token (for gated models)              | `HUGGING_FACE_HUB_TOKEN`                                |

---

## Step 1 — Start TGI

```bash
docker run -d --name tgi \
    --gpus all \
    -p 8080:80 \
    -v $PWD/tgi-data:/data \
    -e HUGGING_FACE_HUB_TOKEN=$HF_TOKEN \
    --shm-size=1g \
    ghcr.io/huggingface/text-generation-inference:latest \
    --model-id mistralai/Mistral-7B-Instruct-v0.3 \
    --max-input-length 4096 \
    --max-total-tokens 8192
```

First run downloads the model into `./tgi-data`; subsequent runs are
fast.

Confirm:

```bash
curl http://localhost:8080/health
# → 200 OK

curl http://localhost:8080/info
# → {"model_id": "...", "model_dtype": "torch.float16", ...}
```

---

## Step 2 — Smoke-test the OpenAI-compatible endpoint

```bash
curl http://localhost:8080/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{
       "model": "tgi",
       "messages": [{"role": "user", "content": "Say hi in five words."}]
     }'
```

Note: TGI exposes models under the symbolic name `"tgi"` regardless of
what you loaded. The `--model-id` flag determines what runs; the API
doesn't expose other names.

---

## Step 3 — Wire AGCL to TGI

Three options:

### Option A — TGI is THE cloud

```bash
echo "TGI_HOST=http://localhost:8080"   >> .env
echo "TGI_MODEL=tgi"                    >> .env
```

The toolkit registry picks TGI over OpenAI/Anthropic when `TGI_HOST`
is set.

### Option B — Custom provider

Setup → Custom OpenAI-compatible providers:

| Field         | Value                                       |
|---------------|---------------------------------------------|
| name          | `tgi-local`                                |
| base_url      | `http://localhost:8080/v1`                 |
| api_key_env   | `TGI_API_KEY` (set to `EMPTY` if no auth)  |
| model         | `tgi`                                       |

### Option C — Behind LiteLLM

```yaml
model_list:
  - model_name: smart
    litellm_params:
      model: huggingface/mistralai/Mistral-7B-Instruct-v0.3
      api_base: http://tgi:8080
```

---

## Step 4 — Verify

```bash
python main.py toolkit ping tgi
# → {"ok": true, "status": 200, "url": "http://localhost:8080/v1"}

python main.py toolkit chat "Hello in five words."
```

Or in the GUI: Toolkit tab → `tgi` row → **Ping**.

---

## Quantization (smaller VRAM footprint)

TGI supports several backends:

```bash
# AWQ (4-bit) — saves ~75% memory, small quality hit
--quantize awq

# GPTQ (4-bit)
--quantize gptq

# EETQ (8-bit, simpler)
--quantize eetq
```

Pass `--quantize` to the `docker run` args. Model must have a
compatible quantization checkpoint (look on the HF model card).

---

## Sharding across GPUs

```bash
docker run … \
    -e NUM_SHARD=4 \
    ghcr.io/huggingface/text-generation-inference:latest \
    --model-id meta-llama/Meta-Llama-3.1-70B-Instruct
```

`NUM_SHARD` should equal the number of GPUs you want to use.

---

## Common problems

| Symptom                                          | Fix                                                                                                |
|--------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| `RuntimeError: CUDA out of memory`               | Lower `--max-input-length` / `--max-total-tokens`; add `--quantize awq`; or use a smaller model.    |
| `HfHubHTTPError: 401`                            | Model gated; set `HUGGING_FACE_HUB_TOKEN`.                                                          |
| AGCL toolkit ping says unreachable               | TGI listens on `:80` inside the container — map it to `:8080:80` on the host.                       |
| OpenAI route returns 404                         | Old TGI image. Pull `:latest` (≥ v2.0 has `/v1/chat/completions`).                                  |
| Streaming chunks arrive but no final stop event  | Old client SDK that doesn't understand TGI's SSE `[DONE]`. AGCL handles this correctly.             |
| Slow first request                                | TGI compiles kernels on first inference. Subsequent requests are fast.                              |

---

## When to switch away

TGI is in maintenance — only critical bug fixes, no new model
support. If you're running it today, plan to migrate to vLLM. Drop-in
replacement: stop the TGI container, start vLLM (see [vllm.md](vllm.md))
on the same port, point AGCL at `VLLM_HOST` instead of `TGI_HOST`.

---

## What to read next

- [vllm.md](vllm.md) — recommended replacement
- [ollama.md](ollama.md) — lighter-weight alternative
- [litellm_gw.md](litellm_gw.md) — put TGI behind a routing proxy
