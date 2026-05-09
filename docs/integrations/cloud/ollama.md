# Ollama — local / LAN / remote inference

<p align="left">
  <a href="https://img.shields.io/badge/Ollama-0.x-000000"><img alt="Ollama" src="https://img.shields.io/badge/Ollama-0.x-000000"></a>
  <a href="https://img.shields.io/badge/format-GGUF-2496ED"><img alt="GGUF" src="https://img.shields.io/badge/format-GGUF-2496ED"></a>
  <img src="../../assets/docker.svg" width="20" alt="Docker" />
  <img src="../../assets/nvidia.svg" width="20" alt="NVIDIA" />
</p>

**What it does:** Single-binary LLM server. Runs GGUF models natively.
Exposes an OpenAI-compatible endpoint at `:11434/v1`. Zero config for
local dev.

**Why it matters for AGCL:** Lets users run llama3, qwen2.5, deepseek-r1,
mistral, gemma3 without any cloud account. AGCL becomes fully
air-gapped capable.

> Part of the **[Cloud / infra integrations](../cloud.md)**.

---

## Install

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows — download installer at https://ollama.com/download

# Docker (server deployments)
docker run -d -v ollama:/root/.ollama -p 11434:11434 ollama/ollama

# With GPU
docker run -d --gpus=all -v ollama:/root/.ollama -p 11434:11434 ollama/ollama
```

---

## Pull models

```bash
ollama pull llama3.3          # Meta Llama 3.3 70B (Q4)
ollama pull qwen2.5:32b
ollama pull deepseek-r1:14b
ollama pull mistral
ollama pull gemma3:27b
ollama pull nomic-embed-text  # for embeddings
```

---

## Remote / LAN Ollama

```bash
# On the server running Ollama — expose on all interfaces
OLLAMA_HOST=0.0.0.0 ollama serve
```

```env
# In AGCL .env — point to LAN or remote host
OLLAMA_HOST=http://192.168.1.50:11434

# Or remote GPU box:
OLLAMA_HOST=http://my-runpod-pod.proxy.runpod.net:11434
```

---

## API format (OpenAI-compatible)

```python
import os
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1",
    api_key="ollama",                         # required but ignored by Ollama
)

response = await client.chat.completions.create(
    model="llama3.3",
    messages=[{"role": "user", "content": prompt}],
    stream=True,
)
```

---

## Via LiteLLM proxy (recommended for AGCL)

```yaml
# litellm_config.yaml
- model_name: local
  litellm_params:
    model: ollama/llama3.3
    api_base: http://192.168.1.50:11434
```

See [litellm.md](litellm.md) for full proxy setup.

---

## Supported GPU cloud targets

| Platform | How to expose Ollama |
|---|---|
| **RunPod** | Use a custom Docker template with Ollama; expose port 11434 via pod proxy URL |
| **Vast.ai** | SSH tunnel or use `--open-port 11434` in instance config |
| **Lambda Labs** | Run `OLLAMA_HOST=0.0.0.0 ollama serve` behind nginx |
| **Paperspace** | Gradient notebook → persistent disk → Ollama in background |
