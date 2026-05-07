"""
Backends for RecursiveAgent.

Two backends are supported. They share a small interface so the loop
controller can drive any combination of them.

    HFBackend     transformers + PyTorch.
                  Full latent injection (via inputs_embeds), token-level
                  logits, and gradient flow. Required for training.

    GGUFBackend   llama-cpp-python.
                  Inference only. The embedding API gives last-token
                  hidden states but llama.cpp has no `inputs_embeds`
                  equivalent — so injected latents are NOT fed back into
                  a gguf agent. The latent still flows through the loop
                  via the inner/outer links applied to the gguf agent's
                  output, so chains like "gguf -> hf -> hf" work; only
                  the gguf agent itself doesn't condition on prior latents.

Interface every backend implements:

    .hidden_size                                  int
    .supports_injection() -> bool                 capability flag
    .encode(text)            -> (ids, attn)       tokenize (HF only; gguf raises)
    .forward_latent_text(text, injected=None)     -> (latent[B,D], logits|None)
    .forward_latent(ids, injected, attn)          -> (latent[B,D], logits|None)
    .decode_text(prompt, injected, max_new_tokens, **kw) -> str
"""

from __future__ import annotations

from typing import Optional, List, Tuple, Union

import torch
import torch.nn as nn


TextLike = Union[str, List[str]]


# ---------- shared base ----------

class _BackendBase(nn.Module):
    hidden_size: int
    name: str = "base"

    def supports_injection(self) -> bool:
        return False

    # token-mode (HF native)
    def encode(self, text: TextLike) -> Tuple[torch.Tensor, torch.Tensor]:
        raise NotImplementedError

    def forward_latent(self, input_ids, injected=None, attention_mask=None):
        raise NotImplementedError

    # text-mode (works for both backends)
    def forward_latent_text(self, text: TextLike, injected: Optional[torch.Tensor] = None):
        raise NotImplementedError

    def decode_text(self, prompt_text: TextLike, injected: Optional[torch.Tensor] = None,
                    max_new_tokens: int = 128, **gen_kwargs) -> str:
        raise NotImplementedError


# ---------- HuggingFace ----------

class HFBackend(_BackendBase):
    name = "hf"

    def __init__(self, model, tokenizer, device: str = "cpu"):
        super().__init__()
        self.model = model
        self.tokenizer = tokenizer
        self.device = device
        self.hidden_size = int(model.config.hidden_size)

    def supports_injection(self) -> bool:
        return True

    def encode(self, text: TextLike) -> Tuple[torch.Tensor, torch.Tensor]:
        if isinstance(text, str):
            text = [text]
        enc = self.tokenizer(text, return_tensors="pt", padding=True, truncation=True)
        return enc.input_ids.to(self.device), enc.attention_mask.to(self.device)

    def forward_latent(self, input_ids, injected=None, attention_mask=None):
        embed = self.model.get_input_embeddings()
        e = embed(input_ids)                           # [B, T, D]
        if injected is not None:
            inj = injected.unsqueeze(1).to(e.dtype).to(e.device)
            e = torch.cat([inj, e], dim=1)
            if attention_mask is not None:
                pad = torch.ones(
                    attention_mask.size(0), 1,
                    device=attention_mask.device, dtype=attention_mask.dtype,
                )
                attention_mask = torch.cat([pad, attention_mask], dim=1)
        out = self.model(
            inputs_embeds=e,
            attention_mask=attention_mask,
            output_hidden_states=True,
            return_dict=True,
        )
        last_hidden = out.hidden_states[-1][:, -1, :]
        return last_hidden, out.logits

    def forward_latent_text(self, text: TextLike, injected=None):
        ids, attn = self.encode(text)
        return self.forward_latent(ids, injected=injected, attention_mask=attn)

    @torch.no_grad()
    def decode_text(self, prompt_text: TextLike, injected=None,
                    max_new_tokens: int = 128, **gen_kwargs) -> str:
        ids, attn = self.encode(prompt_text)
        if injected is None:
            out_ids = self.model.generate(
                input_ids=ids, attention_mask=attn,
                max_new_tokens=max_new_tokens, **gen_kwargs,
            )
            new_ids = out_ids[0, ids.size(1):]
        else:
            # Append the injected latent at the END of the prompt embeddings.
            # Prepending shifts every prompt token's RoPE position by one,
            # which confuses chat-tuned models and often makes them greedy-
            # decode EOS immediately. Appending preserves the prompt's natural
            # positions and lets the latent act as the "next thought" the model
            # generates from.
            embed = self.model.get_input_embeddings()
            e = embed(ids)
            inj = injected.unsqueeze(1).to(e.dtype).to(e.device)
            e = torch.cat([e, inj], dim=1)
            pad = torch.ones(attn.size(0), 1, device=attn.device, dtype=attn.dtype)
            attn = torch.cat([attn, pad], dim=1)
            out_ids = self.model.generate(
                inputs_embeds=e, attention_mask=attn,
                max_new_tokens=max_new_tokens, **gen_kwargs,
            )
            # generate(inputs_embeds=...) returns only the new tokens
            new_ids = out_ids[0]
        return self.tokenizer.decode(new_ids, skip_special_tokens=True)

    @classmethod
    def load(cls, name_or_path: str, dtype=None, device: str = "cpu"):
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as e:
            raise RuntimeError(
                "HFBackend requires transformers. pip install transformers"
            ) from e
        tok = AutoTokenizer.from_pretrained(name_or_path)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token or tok.unk_token
        kw = {}
        if dtype is not None:
            kw["torch_dtype"] = dtype
        m = AutoModelForCausalLM.from_pretrained(name_or_path, **kw).to(device)
        return cls(m, tok, device=device)


# ---------- llama.cpp / GGUF ----------

class GGUFBackend(_BackendBase):
    """
    Inference-only backend for .gguf files via llama-cpp-python.

    Latent injection is not supported by llama.cpp's API, so the
    `injected` argument is ignored at this agent's input. The latent
    still propagates through the wider loop (the gguf agent's own output
    embedding feeds the next OuterLink), so this remains useful as a
    cheap, fixed-weight intermediate or final agent.
    """
    name = "gguf"

    def __init__(self, llama, n_embd: int, model_path: str = ""):
        super().__init__()
        self.llama = llama
        self.hidden_size = int(n_embd)
        self.model_path = model_path

    def supports_injection(self) -> bool:
        return False

    def encode(self, text: TextLike):
        raise NotImplementedError(
            "GGUFBackend operates on text directly; use forward_latent_text/decode_text"
        )

    def forward_latent(self, input_ids, injected=None, attention_mask=None):
        raise NotImplementedError(
            "GGUFBackend cannot consume token tensors; use forward_latent_text"
        )

    @torch.no_grad()
    def forward_latent_text(self, text: TextLike, injected=None):
        if isinstance(text, str):
            text = [text]
        latents = []
        for t in text:
            emb = self.llama.embed(t)
            v = torch.tensor(emb, dtype=torch.float32)
            if v.dim() == 2:        # [n_tokens, hidden] — take last
                v = v[-1]
            latents.append(v)
        return torch.stack(latents, dim=0), None

    @torch.no_grad()
    def decode_text(self, prompt_text: TextLike, injected=None,
                    max_new_tokens: int = 128, **gen_kwargs) -> str:
        if isinstance(prompt_text, list):
            prompt_text = prompt_text[0]
        out = self.llama(
            prompt_text or "",
            max_tokens=max_new_tokens,
            temperature=gen_kwargs.get("temperature", 0.7),
            top_p=gen_kwargs.get("top_p", 0.9),
            top_k=gen_kwargs.get("top_k", 40),
            repeat_penalty=gen_kwargs.get("repeat_penalty", 1.05),
            stop=gen_kwargs.get("stop", []),
            echo=False,
        )
        return out["choices"][0]["text"]

    @classmethod
    def load(cls, model_path: str, n_ctx: int = 2048,
             n_gpu_layers: int = 0, n_threads: int = 4):
        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise RuntimeError(
                "GGUFBackend requires llama-cpp-python."
            ) from e
        llama = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            n_threads=n_threads,
            embedding=True,
            verbose=False,
        )
        # Resolve hidden size from llama-cpp internals (API varies across versions).
        if hasattr(llama, "n_embd") and callable(llama.n_embd):
            n_embd = llama.n_embd()
        elif hasattr(llama, "_model") and hasattr(llama._model, "n_embd"):
            n_embd = llama._model.n_embd()
        else:
            # Fallback: probe with a tiny embed call.
            sample = llama.embed("hello")
            v = torch.tensor(sample, dtype=torch.float32)
            n_embd = v.shape[-1]
        return cls(llama, n_embd=int(n_embd), model_path=model_path)
