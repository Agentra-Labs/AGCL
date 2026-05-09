# vLLM — high-throughput inference backend

<p align="left">
  <a href="https://img.shields.io/badge/vLLM-0.x-FF6F00"><img alt="vLLM" src="https://img.shields.io/badge/vLLM-0.x-FF6F00"></a>
  <a href="https://img.shields.io/badge/throughput-10--50%C3%97%20Ollama-32A852"><img alt="Throughput" src="https://img.shields.io/badge/throughput-10--50%C3%97%20Ollama-32A852"></a>
  <img src="../../assets/docker.svg" width="20" alt="Docker" />
  <img src="../../assets/nvidia.svg" width="20" alt="NVIDIA" />
</p>

**What it does:** Production-grade LLM inference with continuous
batching, tensor parallelism, PagedAttention. Delivers 10–50× higher
throughput than Ollama under concurrent load.

**Why it matters for AGCL:** RecursiveMAS-as-a-service, multi-user
deployments, or any scenario where >1 agent hits the model
simultaneously.

**Model format note:** vLLM uses HuggingFace `.safetensors`, NOT GGUF.
Cannot load Ollama's GGUF files directly.

> Part of the **[Cloud / infra integrations](../cloud.md)**.

---

## Install

```bash
pip install vllm
```

Or Docker (recommended for GPU servers):

```bash
docker run -d \
  --gpus all \
  --ipc=host \
  -p 8000:8000 \
  -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
  vllm/vllm-openai:latest \
  --model meta-llama/Llama-3.3-70B-Instruct \
  --dtype float16 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 8192 \
  --max-num-seqs 256
```

---

## Multi-GPU (tensor parallelism)

```bash
vllm serve meta-llama/Llama-3.3-70B-Instruct \
  --tensor-parallel-size 2 \
  --gpu-memory-utilization 0.90
# Exposes OpenAI-compatible endpoint at :8000
```

---

## Quantization

```bash
# AWQ (fastest on A100/H100)
vllm serve TheBloke/Llama-3-70B-Instruct-AWQ --quantization awq

# FP8 (H100 native)
vllm serve meta-llama/Llama-3.3-70B-Instruct --quantization fp8
```

---

## API format (OpenAI-compatible, identical to Ollama)

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url="http://vllm-host:8000/v1",
    api_key="EMPTY",   # vLLM accepts any value
)
```

---

## Via LiteLLM proxy

```yaml
- model_name: fast-70b
  litellm_params:
    model: hosted_vllm/meta-llama/Llama-3.3-70B-Instruct
    api_base: http://vllm-host:8000
```

See [litellm.md](litellm.md).

---

## Cloud GPU targets

| Platform | vLLM command |
|---|---|
| **RunPod** | Use `runpod/pytorch:2.4.0-py3.11-cuda12.4.1` template → `pip install vllm && vllm serve ...` |
| **Vast.ai** | Select PyTorch image, run vLLM in tmux with `--host 0.0.0.0` |
| **Lambda Labs** | A100 instance → `pip install vllm` → serve on port 8000, expose via Lambda's port config |
| **Paperspace** | A100/H100 machine → vLLM as systemd service or Docker |
