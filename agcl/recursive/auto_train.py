"""
Cloud-bootstrapped auto-training for RecursiveMAS.

The links (InnerLink, OuterLink) ship at random/identity init. An untrained
MAS injects a noise vector into the final agent's decode and the model
typically responds with a single punctuation token. This module fixes that
by training the links *online* using the cloud model as a teacher:

    1. Bootstrap                     ask the cloud once for
                                     (polished_answer, [reformulations]).
    2. Stage A — latent alignment    train inner+outer links so the loop's
                                     final latent matches the final agent's
                                     own encoding of the cloud answer
                                     (cosine-sim objective; agents frozen).
    3. Stage B — answer decode       teacher-force CE on the answer tokens
                                     through the final agent, with the
                                     loop-produced latent injected at the
                                     prompt/answer boundary.

Stage B requires the final agent to be HF-backed. With a GGUF final agent
only Stage A runs.

A small TopicTracker keeps an EMA centroid of recent user turns (encoded
by the planner agent). When cosine sim of a new turn drops below the
threshold, we treat it as a candidate topic switch and ask the cloud to
confirm before retraining.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Callable, List, Optional, Tuple

import torch
import torch.nn.functional as F

from .mas import RecursiveMAS
from .backends import HFBackend
from .control import TrainingControl, HaltedError


# ---------- cloud calls ----------

_BOOTSTRAP_PROMPT = """You are helping train a small local reasoning model on a new topic.

Given the user's question below, return a single JSON object with two keys:
  "answer":          your best answer to the question, 2-6 sentences, plain text.
  "reformulations":  a list of 6 different ways to ask the SAME question
                     (vary wording and structure but preserve meaning).

User question: {question}

Reply with the JSON object only. No markdown fences, no commentary."""


_SWITCH_JUDGE_PROMPT = """Two consecutive user turns from a chat:

[A] {prev}
[B] {curr}

Is [B] a TOPIC SWITCH from [A]?
- A follow-up that drills deeper into the SAME subject is NOT a switch.
- A question about a different subject IS a switch.

Reply with exactly one word: yes or no."""


async def _cloud_call(prompt: str, provider: Optional[str] = None,
                      max_tokens: int = 1024,
                      session_id: Optional[str] = None,
                      kind: str = "knowledge") -> str:
    """Plain single-turn cloud call (no continuation framing).

    All bootstrap teacher answers + reformulation requests + topic-switch
    judgements run through here; tagged `kind="knowledge"` by default so
    the dashboard can split them out from regular chat usage.
    """
    from agcl.config import (
        OPENAI_API_KEY, ANTHROPIC_API_KEY,
        OPENAI_MODEL, CLAUDE_MODEL, DEFAULT_CLOUD,
    )
    from agcl import usage as _usage
    import time as _t
    provider = provider or DEFAULT_CLOUD
    _usage.check_quota(provider)

    t0 = _t.time()
    out = ""
    in_tokens_real = out_tokens_real = None
    err = None
    client = None     # held outside the try so the finally can aclose it
    try:
        if provider == "openai":
            import openai
            client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
            resp = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            out = (resp.choices[0].message.content or "").strip()
            u = getattr(resp, "usage", None)
            if u is not None:
                in_tokens_real  = getattr(u, "prompt_tokens", None)
                out_tokens_real = getattr(u, "completion_tokens", None)
            return out
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [b.text for b in resp.content if hasattr(b, "text")]
        out = "".join(parts).strip()
        u = getattr(resp, "usage", None)
        if u is not None:
            in_tokens_real  = getattr(u, "input_tokens", None)
            out_tokens_real = getattr(u, "output_tokens", None)
        return out
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        raise
    finally:
        # Close the SDK's owned httpx pool while the asyncio loop is
        # still alive — otherwise GC closes it after asyncio.run() has
        # already torn the loop down ("Event loop is closed" tracebacks).
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
        try:
            _usage.record_call(
                provider=provider,
                model=OPENAI_MODEL if provider == "openai" else CLAUDE_MODEL,
                kind=kind, session_id=session_id,
                in_tokens=in_tokens_real, out_tokens=out_tokens_real,
                in_text=prompt if in_tokens_real is None else None,
                out_text=out if out_tokens_real is None else None,
                latency_sec=_t.time() - t0, ok=err is None, error=err,
            )
        except Exception:
            pass


def _parse_bootstrap(raw: str, fallback_q: str,
                     n: int) -> Tuple[str, List[str]]:
    """Best-effort JSON extraction from a possibly-fenced reply."""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return raw.strip() or fallback_q, [fallback_q]
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return raw.strip() or fallback_q, [fallback_q]
    ans = (d.get("answer") or "").strip() or raw.strip() or fallback_q
    refs = [r.strip() for r in (d.get("reformulations") or [])
            if isinstance(r, str) and r.strip()]
    if not refs:
        refs = [fallback_q]
    return ans, refs[:n]


async def bootstrap_pairs(question: str, n_reformulations: int = 6,
                          provider: Optional[str] = None
                          ) -> Tuple[str, List[str]]:
    """Ask cloud once for an answer + N reformulations of the question."""
    raw = await _cloud_call(
        _BOOTSTRAP_PROMPT.format(question=question), provider=provider,
    )
    return _parse_bootstrap(raw, question, n_reformulations)


async def cloud_confirm_switch(prev: str, curr: str,
                               provider: Optional[str] = None) -> bool:
    """Single yes/no cloud call to confirm a candidate topic switch."""
    raw = await _cloud_call(
        _SWITCH_JUDGE_PROMPT.format(prev=prev or "(no prior turn)", curr=curr),
        provider=provider, max_tokens=8,
    )
    return raw.lower().lstrip().startswith("y")


# ---------- training ----------

def _freeze_agents(mas: RecursiveMAS):
    """Set requires_grad=False on every agent param; return restore fn."""
    saved = []
    for a in mas.agents:
        for p in a.parameters():
            saved.append((p, p.requires_grad))
            p.requires_grad = False
    def restore():
        for p, rg in saved:
            p.requires_grad = rg
    return restore


def auto_train(mas: RecursiveMAS, question: str, answer: str,
               reformulations: List[str],
               stage1_steps: int = 30, stage2_steps: int = 20,
               lr1: float = 1e-2, lr2: float = 1e-3,
               verbose: bool = True,
               on_progress: Optional[Callable[[dict], None]] = None,
               control: Optional[TrainingControl] = None) -> dict:
    """
    Run two-stage cloud-teacher training in place. Returns a dict of
    per-step losses for each stage.

    Stage A trains the loop's inner+outer links so the final latent's
    cosine similarity to the final agent's encoding of `answer` increases.
    Stage B teacher-forces the answer tokens through the final HF agent
    with the loop-produced latent appended at the prompt boundary.
    """
    final_agent = mas.agents[-1]

    def _emit(ev: dict) -> None:
        if on_progress is not None:
            try:
                on_progress(ev)
            except Exception:
                pass  # progress callbacks must never break training

    def _gate(stage: str, step: int) -> None:
        """Honor pause/halt requests at a safe point in the loop."""
        if control is None:
            return
        control.mark(stage, step)
        if control.is_paused():
            _emit({"event": "paused", "stage": stage, "step": step})
            control.wait_if_paused()
            if not control.is_halted():
                _emit({"event": "resumed", "stage": stage, "step": step})
        if control.should_halt():
            _emit({"event": "halted", "stage": stage, "step": step})
            raise HaltedError(f"halted during {stage} step {step}")

    restore = _freeze_agents(mas)
    try:
        # ---- target latent (no grad) ----
        with torch.no_grad():
            target_latent, _ = final_agent.forward_latent_text(answer)
        target_latent = target_latent.detach()             # [1, D_final]

        link_params = (list(mas.inner.parameters())
                       + list(mas.outer.parameters()))
        prompts = list(reformulations) + [question]
        if not prompts:
            prompts = [question]

        # ---- stage A: cosine alignment ----
        s1: List[float] = []
        if stage1_steps > 0:
            _emit({"event": "stage1_start", "total_steps": stage1_steps})
            opt = torch.optim.AdamW(link_params, lr=lr1)
            for step in range(stage1_steps):
                _gate("stage1", step)
                text = prompts[step % len(prompts)]
                latent, _ = mas.run_latent_text(text)
                if control is not None:
                    control.record_latent(latent)
                tgt = target_latent.expand_as(latent)
                loss = (1.0 - F.cosine_similarity(latent, tgt, dim=-1)).mean()
                opt.zero_grad(); loss.backward(); opt.step()
                v = float(loss.item())
                s1.append(v)
                _emit({"event": "stage1_step", "step": step + 1,
                       "total_steps": stage1_steps, "loss": v})
                if verbose and (step == 0 or step + 1 == stage1_steps
                                or (step + 1) % 10 == 0):
                    print(f"  [stage A] {step+1:3d}/{stage1_steps}  cos-loss={v:.4f}")
            _emit({"event": "stage1_done",
                   "first_loss": s1[0] if s1 else None,
                   "final_loss": s1[-1] if s1 else None})

        # ---- stage B: token CE through final HF agent ----
        s2: List[float] = []
        backend = final_agent.backend
        if stage2_steps > 0 and isinstance(backend, HFBackend):
            tok = backend.tokenizer
            model = backend.model
            ans_ids = tok(answer, return_tensors="pt",
                          truncation=True).input_ids[0]
            if ans_ids.numel() < 2:
                if verbose:
                    print("  [stage B] answer < 2 tokens, skipping")
                _emit({"event": "stage2_skipped",
                       "reason": "answer tokenized to < 2 tokens"})
            else:
                _emit({"event": "stage2_start", "total_steps": stage2_steps})
                opt2 = torch.optim.AdamW(link_params, lr=lr2)
                embed = model.get_input_embeddings()
                for step in range(stage2_steps):
                    _gate("stage2", step)
                    text = prompts[step % len(prompts)]
                    latent, _ = mas.run_latent_text(text)         # [1, D]
                    if control is not None:
                        control.record_latent(latent)
                    prompt_ids = tok(text, return_tensors="pt",
                                     truncation=True).input_ids
                    full_ids = torch.cat(
                        [prompt_ids, ans_ids.unsqueeze(0)], dim=1,
                    )
                    e = embed(full_ids)
                    T_p = prompt_ids.size(1)
                    inj = latent.unsqueeze(1).to(e.dtype).to(e.device)
                    e_full = torch.cat(
                        [e[:, :T_p], inj, e[:, T_p:]], dim=1,
                    )
                    out = model(inputs_embeds=e_full, return_dict=True)
                    logits = out.logits                            # [1, T, V]
                    T_a = ans_ids.size(0)
                    # logits at positions [T_p .. T_p+T_a-1] predict
                    # answer tokens [0 .. T_a-1]
                    ans_logits = logits[:, T_p:T_p + T_a, :]
                    loss = F.cross_entropy(
                        ans_logits.reshape(-1, ans_logits.size(-1)),
                        ans_ids.to(ans_logits.device),
                    )
                    opt2.zero_grad(); loss.backward(); opt2.step()
                    v = float(loss.item())
                    s2.append(v)
                    _emit({"event": "stage2_step", "step": step + 1,
                           "total_steps": stage2_steps, "loss": v})
                    if verbose and (step == 0 or step + 1 == stage2_steps
                                    or (step + 1) % 5 == 0):
                        print(f"  [stage B] {step+1:3d}/{stage2_steps}  ce={v:.4f}")
                _emit({"event": "stage2_done",
                       "first_loss": s2[0] if s2 else None,
                       "final_loss": s2[-1] if s2 else None})
        elif stage2_steps > 0:
            if verbose:
                print("  [stage B] final agent is GGUF — skipped")
            _emit({"event": "stage2_skipped",
                   "reason": "final agent is GGUF"})

        return {"stage1_losses": s1, "stage2_losses": s2}
    finally:
        restore()


# ---------- topic tracking ----------

class TopicTracker:
    """
    Tracks an EMA centroid of recent user-turn embeddings. `update_and_check`
    returns True when the new turn's cosine similarity to the centroid drops
    below `threshold` (a *candidate* switch — caller decides whether to
    confirm with the cloud). After update, the centroid is folded toward
    the new turn so the EMA tracks the conversation.
    """

    def __init__(self, encode_fn: Callable[[str], torch.Tensor],
                 threshold: float = 0.6, momentum: float = 0.7):
        self.encode = encode_fn
        self.threshold = float(threshold)
        self.momentum = float(momentum)
        self.centroid: Optional[torch.Tensor] = None
        self.last_text: str = ""

    @torch.no_grad()
    def _embed(self, text: str) -> torch.Tensor:
        v = self.encode(text)
        if v.dim() == 2:
            v = v.squeeze(0)
        return v.detach()

    def update_and_check(self, text: str) -> Tuple[bool, float]:
        v = self._embed(text)
        sim = 1.0
        candidate = False
        if self.centroid is not None:
            sim = float(F.cosine_similarity(
                v.unsqueeze(0), self.centroid.unsqueeze(0), dim=-1,
            ).item())
            candidate = sim < self.threshold
        # fold the new turn into the centroid AFTER measuring
        if self.centroid is None:
            self.centroid = v.clone()
        else:
            self.centroid = (self.momentum * self.centroid
                             + (1 - self.momentum) * v)
        self.last_text = text
        return candidate, sim

    def reset(self, text: str) -> None:
        """Hard reset: a new topic has been confirmed."""
        self.centroid = self._embed(text)
        self.last_text = text
