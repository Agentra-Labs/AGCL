# HuggingFace TGI — legacy / HF Endpoints

<p align="left">
  <img src="../../assets/huggingface.svg" width="22" alt="Hugging Face" />&nbsp;
  <a href="https://img.shields.io/badge/status-maintenance%20mode-yellow"><img alt="Status" src="https://img.shields.io/badge/status-maintenance%20mode-yellow"></a>
  <a href="https://img.shields.io/badge/successor-vLLM%20%2F%20SGLang-FF6F00"><img alt="Successor" src="https://img.shields.io/badge/successor-vLLM%20%2F%20SGLang-FF6F00"></a>
</p>

> **Status note:** TGI entered maintenance mode on December 11, 2025.
> HuggingFace now recommends vLLM or SGLang for Inference Endpoints.
> TGI still works and existing deployments are valid, but new projects
> should prefer **[vLLM](vllm.md)**.

**What it does:** HF's Rust + Python gRPC model server. Exposes
`/v1/chat/completions` (OpenAI-compatible). Good for RunPod / HF
Endpoints legacy deployments.

> Part of the **[Cloud / infra integrations](../cloud.md)**.

---

## Install / run

```bash
docker run -d \
  --gpus all \
  -p 8080:80 \
  -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
  ghcr.io/huggingface/text-generation-inference:latest \
  --model-id meta-llama/Llama-3.1-8B-Instruct \
  --max-total-tokens 4096 \
  --max-batch-total-tokens 16384
```

---

## API format (OpenAI-compatible)

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "tgi", "messages": [{"role": "user", "content": "Hello"}], "stream": true}'
```

---

## Via LiteLLM

```yaml
- model_name: tgi-llama
  litellm_params:
    model: huggingface/meta-llama/Llama-3.1-8B-Instruct
    api_base: http://tgi-host:8080
    api_key: hf_xxx
```

See [litellm.md](litellm.md).

---

## Migration path: TGI → vLLM

```bash
# TGI (old)
docker run ghcr.io/huggingface/text-generation-inference:latest --model-id <model>

# vLLM (new — same OpenAI-compatible endpoint)
docker run vllm/vllm-openai:latest --model <model>

# Change api_base in litellm_config.yaml — nothing else changes
```
