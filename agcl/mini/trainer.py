"""
MiniTrainer - cooperative background training loop.

Single-thread design: when started, spins one daemon thread that:
  1. drains its sample buffer in mini-batches,
  2. takes a single optimizer step,
  3. sleeps `yield_ms` so the main MAS keeps CPU,
  4. checkpoints every `ckpt_interval` steps.

Pause / resume / stop are thread-safe; status() and test() are
hot-callable from any thread (a per-model lock guards reads from
`generate()` and writes from the training step).

The trainer is intentionally tolerant: a bad sample that explodes a
loss is logged and skipped, never killed. Lost samples are preferable
to a dead trainer.
"""

from __future__ import annotations

import collections
import json
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import torch

from .config import MiniConfig
from .model import build_arch
from .strategies import compose_loss, PRESETS
from . import tokenizer as tok


class MiniTrainer:
    def __init__(self, config: Optional[MiniConfig] = None) -> None:
        self.config = config or MiniConfig()
        tok.init(self.config.vocab_size)

        self.model = build_arch(self.config)
        self.opt = torch.optim.AdamW(self.model.parameters(),
                                      lr=self.config.lr)

        self.buffer: Deque[Dict[str, Any]] = collections.deque(
            maxlen=self.config.buffer_size,
        )

        self._lock = threading.Lock()      # guards model + opt
        self._buffer_lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.step = 0
        self.last_loss: Optional[float] = None
        self.loss_history: List[float] = []
        self.error: Optional[str] = None
        self.started_at: Optional[float] = None
        self.samples_seen = 0

        # Best-effort load of any saved checkpoint.
        self._try_load()

    # --- lifecycle ---------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._paused.clear()
        self.started_at = time.time()
        self.error = None
        self._thread = threading.Thread(
            target=self._loop, name="agcl-mini-trainer", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._paused.clear()
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)
        self._thread = None

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- ingestion ---------------------------------------------------

    def append_sample(self, *, prompt: str, output: str,
                      latent: Optional[torch.Tensor] = None,
                      reformulations: Optional[List[str]] = None,
                      reasoning: Optional[str] = None) -> None:
        """
        Append a (prompt, output, latent?, reformulations?) tuple to the
        training buffer. Called from RecursiveSession.turn() after each
        turn so training keeps up with usage.
        """
        if not self.config.enabled:
            return
        sample = {
            "prompt": prompt or "",
            "output": output or "",
            "reasoning": reasoning or "",
            "reformulations": list(reformulations or []),
            "latent": (latent.detach().cpu().clone()
                        if latent is not None else None),
            "ts": time.time(),
        }
        with self._buffer_lock:
            self.buffer.append(sample)
        self.samples_seen += 1

    # --- the loop ----------------------------------------------------

    def _loop(self) -> None:
        # Lower OS priority so main work has CPU. POSIX-only; ignore on
        # platforms without nice().
        try:
            os.nice(self.config.nice)
        except Exception:
            pass

        # Limit torch's intra-op parallelism so we don't fight the main
        # MAS for every core.
        try:
            torch.set_num_threads(max(1, torch.get_num_threads() // 2))
        except Exception:
            pass

        while not self._stop.is_set():
            try:
                if self._paused.is_set():
                    time.sleep(0.5)
                    continue
                if not self._has_enough():
                    time.sleep(self.config.yield_ms / 1000.0)
                    continue
                self._step_once()
                time.sleep(self.config.yield_ms / 1000.0)
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                # Don't die - sleep a bit and try again. The error is
                # surfaced by status().
                time.sleep(2.0)

    def _has_enough(self) -> bool:
        with self._buffer_lock:
            return len(self.buffer) >= self.config.batch_size

    # --- one optimizer step ------------------------------------------

    def _step_once(self) -> None:
        batch = self._sample_batch()
        if batch is None:
            return
        with self._lock:
            self.model.train()
            loss = compose_loss(self.model, batch, self.config.strategy)
            if not torch.isfinite(loss):
                self.opt.zero_grad()
                return
            self.opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            self.opt.step()

        self.step += 1
        self.last_loss = float(loss.item())
        self.loss_history.append(self.last_loss)
        if len(self.loss_history) > 1000:
            self.loss_history = self.loss_history[-1000:]

        if self.step % self.config.ckpt_interval == 0:
            self._checkpoint()

    def _sample_batch(self) -> Optional[Dict[str, Any]]:
        with self._buffer_lock:
            if len(self.buffer) < self.config.batch_size:
                return None
            # Pull most recent samples (so the model is biased toward
            # what the user is currently working on).
            samples = list(self.buffer)[-self.config.batch_size:]

        max_seq = self.config.max_seq
        in_ids: List[List[int]] = []
        out_ids: List[List[int]] = []
        latents: List[torch.Tensor] = []
        reform_pairs: List[tuple] = []

        for s in samples:
            in_ids.append(tok.encode(s["prompt"], max_len=max_seq))
            out_ids.append(tok.encode(s["output"], max_len=max_seq))
            if s.get("latent") is not None:
                latents.append(s["latent"].float().reshape(1, -1))
            for r in s.get("reformulations", [])[:2]:
                r_in = tok.encode(r, max_len=max_seq)
                if r_in:
                    reform_pairs.append((
                        torch.tensor([r_in], dtype=torch.long),
                        torch.tensor([r_in], dtype=torch.long),
                    ))

        ids_in_t  = _pad_to_tensor(in_ids,  pad=0, max_seq=max_seq)
        ids_tgt_t = _pad_to_tensor(out_ids, pad=0, max_seq=max_seq)

        latent_t: Optional[torch.Tensor] = None
        if latents:
            # If shapes differ, pick the most common D and skip others.
            dims = [l.shape[-1] for l in latents]
            d = max(set(dims), key=dims.count)
            ok = [l for l in latents if l.shape[-1] == d]
            latent_t = torch.cat(ok, dim=0)

        return {
            "input_ids":   ids_in_t,
            "target_ids":  ids_tgt_t,
            "latent":      latent_t,
            "reform_ids":  reform_pairs or None,
        }

    # --- inference / test --------------------------------------------

    @torch.no_grad()
    def test(self, prompt: str, max_new: int = 32,
             temperature: float = 0.8) -> Dict[str, Any]:
        ids = tok.encode(prompt, max_len=self.config.max_seq)
        if not ids:
            ids = [0]
        x = torch.tensor([ids], dtype=torch.long)
        with self._lock:
            self.model.eval()
            out = self.model.generate(x, max_new=max_new, temperature=temperature)
        out_ids = out[0].tolist()
        new_ids = out_ids[len(ids):]
        return {
            "input_ids":  ids,
            "output_ids": new_ids,
            "decoded":    tok.decode(new_ids),
            "step":       self.step,
            "arch":       getattr(self.model, "arch_name", "unknown"),
            "strategy":   self.config.strategy,
        }

    # --- persistence -------------------------------------------------

    def _ckpt_path(self) -> Path:
        return Path(self.config.state_dir) / "model.pt"

    def _meta_path(self) -> Path:
        return Path(self.config.state_dir) / "meta.json"

    def _checkpoint(self) -> None:
        try:
            p = self._ckpt_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                torch.save({
                    "model":  self.model.state_dict(),
                    "opt":    self.opt.state_dict(),
                    "step":   self.step,
                    "config": self.config.to_dict(),
                }, p)
            self._meta_path().write_text(json.dumps({
                "step":      self.step,
                "last_loss": self.last_loss,
                "samples":   self.samples_seen,
                "arch":      getattr(self.model, "arch_name", "unknown"),
                "strategy":  self.config.strategy,
                "saved_at":  time.time(),
            }, indent=2))
        except Exception as e:
            self.error = f"checkpoint failed: {e}"

    def _try_load(self) -> None:
        p = self._ckpt_path()
        if not p.exists():
            return
        try:
            blob = torch.load(p, map_location="cpu", weights_only=False)
            with self._lock:
                self.model.load_state_dict(blob["model"], strict=True)
                self.opt.load_state_dict(blob["opt"])
            self.step = int(blob.get("step", 0))
        except Exception as e:
            # Shape mismatch (e.g. arch/hidden changed since last run) or
            # corrupt blob. Move the file aside so the next start is clean
            # rather than surfacing a wall of size-mismatch text every call.
            short = type(e).__name__
            try:
                bak = p.with_suffix(p.suffix + ".bak")
                p.rename(bak)
                self.error = f"checkpoint incompatible ({short}); moved to {bak.name}"
            except Exception:
                self.error = f"checkpoint load failed ({short}); kept on disk"

    def force_checkpoint(self) -> None:
        self._checkpoint()

    # --- introspection -----------------------------------------------

    def status(self) -> Dict[str, Any]:
        with self._buffer_lock:
            buf = len(self.buffer)
        recent = self.loss_history[-20:]
        return {
            "enabled":       self.config.enabled,
            "running":       self.is_running(),
            "paused":        self._paused.is_set(),
            "step":          self.step,
            "last_loss":     self.last_loss,
            "loss_recent":   recent,
            "samples_seen":  self.samples_seen,
            "buffer":        buf,
            "buffer_max":    self.config.buffer_size,
            "arch":          getattr(self.model, "arch_name", "unknown"),
            "strategy":      self.config.strategy,
            "attention":     self.config.attention,
            "started_at":    self.started_at,
            "error":         self.error,
        }


# ----------------------------------------------------------------------

def _pad_to_tensor(seqs: List[List[int]], pad: int, max_seq: int) -> torch.Tensor:
    """Right-pad a list of variable-length token id sequences."""
    seqs = [s if s else [pad] for s in seqs]
    L = min(max(len(s) for s in seqs), max_seq)
    out = []
    for s in seqs:
        s = s[:L]
        if len(s) < L:
            s = s + [pad] * (L - len(s))
        out.append(s)
    return torch.tensor(out, dtype=torch.long)
