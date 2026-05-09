# LiteLLM — provider abstraction gateway

<p align="left">
  <a href="https://img.shields.io/badge/LiteLLM-1.x-191E29"><img alt="LiteLLM" src="https://img.shields.io/badge/LiteLLM-1.x-191E29"></a>
  <a href="https://img.shields.io/badge/providers-100%2B-32A852"><img alt="Providers" src="https://img.shields.io/badge/providers-100%2B-32A852"></a>
  <img src="../../assets/anthropic.svg" width="20" alt="Anthropic" />
  <img src="../../assets/openai.svg" width="20" alt="OpenAI" />
  <img src="../../assets/huggingface.svg" width="20" alt="HuggingFace" />
</p>

**What it does:** One unified `completion()` call → 100+ providers.
Drop-in replacement for the OpenAI client. Self-hosted proxy exposes a
single `/v1/chat/completions` endpoint your entire app points to.

**Why it's #1 for AGCL:** You write one inference interface; swap
Anthropic → Groq → Ollama → vLLM → DeepInfra without touching agent
logic. Also handles retry, fallback chains, spend tracking, and
virtual keys.

> Part of the **[Cloud / infra integrations](../cloud.md)**.

---

## Install

```bash
pip install litellm                              # SDK mode
pip install 'litellm[proxy]'                     # Proxy server mode
docker pull ghcr.io/berriai/litellm:main-stable  # Production Docker
```

---

## SDK usage (in AGCL agent core)

```python
from litellm import completion

# Works identically for every provider — just change the model string
response = completion(
    model="anthropic/claude-sonnet-4-5",   # or "groq/llama3-8b-8192"
    messages=[{"role": "user", "content": prompt}]
)

# Providers follow <provider>/<model> naming:
#   openai/gpt-4o · anthropic/claude-opus-4 · groq/mixtral-8x7b
#   ollama/llama3 · hosted_vllm/meta-llama/Llama-3-70B
#   together/mistralai/Mistral-7B
#   fireworks_ai/accounts/fireworks/models/deepseek-v3
#   deepinfra/google/gemma-2-27b-it
#   openrouter/mistralai/mistral-7b-instruct
#   cerebras/llama3.1-8b · gemini/gemini-2.5-pro
```

---

## Self-hosted proxy (`litellm_config.yaml`)

```yaml
model_list:
  - model_name: fast            # alias AGCL uses
    litellm_params:
      model: groq/llama3-8b-8192
      api_key: os.environ/GROQ_API_KEY

  - model_name: smart
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: local
    litellm_params:
      model: ollama/llama3
      api_base: http://localhost:11434

  - model_name: fast            # fallback pool — same alias, different backend
    litellm_params:
      model: hosted_vllm/meta-llama/Llama-3.3-70B-Instruct
      api_base: http://vllm-host:8000

router_settings:
  routing_strategy: latency-based-routing
  fallbacks:
    - model_name: smart
      fallback_models: [fast, local]

general_settings:
  master_key: sk-agcl-internal
```

```bash
litellm --config litellm_config.yaml --port 4000
```

Or via Docker (Compose-ready):

```bash
docker run -d --name litellm -p 4000:4000 \
  -v $(pwd)/litellm_config.yaml:/app/config.yaml:ro \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  ghcr.io/berriai/litellm:main-stable \
  --config /app/config.yaml --port 4000
```

---

## AGCL env vars

```env
AGCL_LLM_BASE_URL=http://localhost:4000
AGCL_LLM_API_KEY=sk-agcl-internal
AGCL_LLM_MODEL=smart           # or fast / local
```

---

## Wire format (output)

OpenAI Chat Completions JSON — identical regardless of upstream
provider:

```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "choices": [{ "message": { "role": "assistant", "content": "..." }, "finish_reason": "stop" }],
  "model": "claude-sonnet-4-6",
  "usage": { "prompt_tokens": 12, "completion_tokens": 80 }
}
```
