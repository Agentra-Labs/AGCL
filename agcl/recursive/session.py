"""
RecursiveSession — multi-turn driver around RecursiveMAS.

Per-turn flow, in order:

  1. Topic gate.
       Embed the user turn with the planner agent's encoder. If cosine
       sim to the EMA centroid drops below `switch_threshold`, ask cloud
       to confirm a topic switch. On first turn, or confirmed switch,
       fall through to (2).

  2. Topic resolution.
       Before training, query the persistent topic index by the planner-
       embedding of the user turn. If a saved topic's centroid scores
       >= `retrieval_threshold` AND its dim signature matches the current
       MAS, load its trained link weights — no retraining.
       Otherwise: cloud bootstrap -> two-stage training -> persist.

  3. Local generation (steady state).
       a) plain mode:        mas.generate_text(prompt, max_new_tokens).
       b) continuator mode:  mas.generate_text(prompt, max_new_tokens=K)
                             becomes a prefix; cloud.stream_continuation
                             finishes from there (collected synchronously
                             so RecursiveSession.turn returns the full
                             string for CLI ergonomics).
       If output is degenerate (too short / pure punctuation / single-
       token repetition), auto-fall back to a clean cloud answer.

  4. Cloud override.
       `force_cloud=True` (or `/cloud` from the CLI) skips local entirely.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Dict, List, Optional

import torch

from .mas import RecursiveMAS
from .auto_train import (
    TopicTracker, bootstrap_pairs, cloud_confirm_switch, auto_train,
)
from .control import TrainingControl, HaltedError
from . import persistence as P


def _looks_degenerate(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 8:
        return True
    if all(c in ".,?!:;-—…\"' \n\t" for c in s):
        return True
    words = s.split()
    if len(words) >= 4 and len(set(words)) == 1:
        return True
    return False


async def _cloud_full(history: List[Dict[str, str]],
                      provider: Optional[str]) -> str:
    """Plain cloud answer for the latest user turn (no local prefix)."""
    from agcl.cloud import one_shot
    return (await one_shot(history, provider=provider)).strip()


async def _cloud_continue_prefix(history: List[Dict[str, str]],
                                  prefix: str,
                                  provider: Optional[str]) -> str:
    """
    Cloud finishes from the given prefix (open assistant turn).
    Returns prefix + continuation.
    """
    from agcl.cloud import stream_continuation
    parts: List[str] = [prefix]
    async for chunk in stream_continuation(history, prefix, provider=provider,
                                            recovery_mode="natural"):
        parts.append(chunk)
    return "".join(parts).strip()


class RecursiveSession:
    def __init__(self, mas: RecursiveMAS, provider: Optional[str] = None,
                 switch_threshold: float = 0.6,
                 retrieval_threshold: float = 0.75,
                 stage1_steps: int = 30, stage2_steps: int = 20,
                 n_reformulations: int = 6,
                 max_new_tokens: int = 128,
                 cloud_continue: bool = False,
                 prefix_tokens: int = 12,
                 persist: bool = True,
                 state_dir: Optional[str] = None,
                 verbose: bool = True,
                 control: Optional[TrainingControl] = None):
        self.mas = mas
        self.provider = provider
        self.verbose = verbose

        self.switch_threshold = switch_threshold
        self.retrieval_threshold = retrieval_threshold
        self.stage1_steps = stage1_steps
        self.stage2_steps = stage2_steps
        self.n_reformulations = n_reformulations

        self.max_new_tokens = max_new_tokens
        self.cloud_continue = cloud_continue
        self.prefix_tokens = prefix_tokens

        self.persist = persist
        if state_dir is None:
            from agcl.config import STATE_DIR
            state_dir = STATE_DIR
        self.state_dir = state_dir

        self.history: List[Dict[str, str]] = []
        self.trained_once = False
        self.topic_seed: Optional[str] = None
        self.current_topic_id: Optional[str] = None
        self.control = control if control is not None else TrainingControl()

        planner = self.mas.agents[0]

        @torch.no_grad()
        def _encode(text: str) -> torch.Tensor:
            v, _ = planner.forward_latent_text(text)
            return v.float()
        self._encode = _encode
        self.tracker = TopicTracker(_encode, threshold=switch_threshold)

    # --------------- public API ---------------

    async def turn(self, user_msg: str, *, force_cloud: bool = False,
                   force_continue: bool = False,
                   on_progress: Optional[Callable[[dict], None]] = None) -> str:
        def _emit(ev: dict) -> None:
            if on_progress is not None:
                try:
                    on_progress(ev)
                except Exception:
                    pass

        _emit({"event": "turn_start",
               "force_cloud": force_cloud,
               "force_continue": force_continue})

        # --- override: cloud only (highest priority, even on first turn) ---
        # force_cloud means "this turn is a pass-through to the cloud — do not
        # train, do not switch topics, do not change session state". Honoring
        # this on the very first turn matters because GUIs use it as an escape
        # hatch and users don't want it to trigger an expensive bootstrap.
        if force_cloud:
            _emit({"event": "cloud_only_start"})
            history = self.history + [{"role": "user", "content": user_msg}]
            ans = await _cloud_full(history, self.provider)
            self.history = history + [{"role": "assistant", "content": ans}]
            _emit({"event": "cloud_only_done", "chars": len(ans)})
            return ans

        # --- (1) topic gate ---
        switched = False
        if self.trained_once:
            candidate, sim = self.tracker.update_and_check(user_msg)
            _emit({"event": "topic_gate", "sim": sim,
                   "threshold": self.switch_threshold,
                   "candidate": candidate})
            if self.verbose and candidate:
                print(f"[agcl] embedding sim={sim:.2f} below "
                      f"{self.switch_threshold:.2f} — confirming with cloud...")
            if candidate:
                switched = await cloud_confirm_switch(
                    self.tracker.last_text, user_msg, provider=self.provider,
                )
                _emit({"event": "topic_switch_confirmed", "switched": switched})
                if self.verbose:
                    print(f"[agcl] cloud says switch: "
                          f"{'yes' if switched else 'no'}")

        # --- (2) bootstrap or retrieve ---
        if not self.trained_once or switched:
            return await self._resolve_topic(user_msg, is_switch=switched,
                                              on_progress=on_progress)

        # --- (3) local generation ---
        use_continue = self.cloud_continue or force_continue
        _emit({"event": "generation_start",
               "mode": "continuator" if use_continue else "local"})
        if self.verbose:
            mode = "local + cloud continuator" if use_continue else "local only"
            print(f"[agcl] running ({mode})...")

        if use_continue:
            prefix = self.mas.generate_text(
                user_msg, max_new_tokens=self.prefix_tokens,
            ) or ""
            prefix = prefix.strip()
            if _looks_degenerate(prefix):
                if self.verbose:
                    print(f"[agcl] local prefix degenerate "
                          f"({prefix!r}); cloud will start clean")
                _emit({"event": "prefix_dropped", "prefix": prefix})
                prefix = ""
            else:
                _emit({"event": "prefix", "text": prefix})
            history = self.history + [{"role": "user", "content": user_msg}]
            out = await _cloud_continue_prefix(history, prefix, self.provider)
            self.history = history + [{"role": "assistant", "content": out}]
            _emit({"event": "generation_done", "mode": "continuator",
                   "chars": len(out)})
            return out

        out = (self.mas.generate_text(
            user_msg, max_new_tokens=self.max_new_tokens,
        ) or "").strip()
        if _looks_degenerate(out):
            if self.verbose:
                print(f"[agcl] local output degenerate "
                      f"({out!r}) — falling back to cloud")
            _emit({"event": "fallback_to_cloud", "local_output": out})
            history = self.history + [{"role": "user", "content": user_msg}]
            cloud = await _cloud_full(history, self.provider)
            self.history = history + [{"role": "assistant", "content": cloud}]
            _emit({"event": "generation_done", "mode": "cloud_fallback",
                   "chars": len(cloud)})
            return cloud

        self.history.append({"role": "user", "content": user_msg})
        self.history.append({"role": "assistant", "content": out})
        _emit({"event": "generation_done", "mode": "local", "chars": len(out)})
        return out

    # --------------- internals ---------------

    async def _resolve_topic(self, user_msg: str, is_switch: bool,
                             on_progress: Optional[Callable[[dict], None]] = None) -> str:
        """Try retrieval; if no hit, bootstrap+train+persist."""
        def _emit(ev: dict) -> None:
            if on_progress is not None:
                try:
                    on_progress(ev)
                except Exception:
                    pass

        # Attempt retrieval first.
        if self.persist:
            _emit({"event": "retrieval_lookup"})
            with torch.no_grad():
                q = self._encode(user_msg).squeeze(0)
            sig = P._signature(self.mas)
            hit = P.find_similar_topic(
                self.state_dir, q,
                threshold=self.retrieval_threshold, signature=sig,
            )
            if hit is not None:
                tid, sim, meta = hit
                try:
                    P.load_topic(self.state_dir, self.mas, tid)
                except Exception as e:
                    if self.verbose:
                        print(f"[agcl] retrieval candidate {tid} "
                              f"failed to load ({e!r}); training fresh")
                    _emit({"event": "retrieval_load_failed",
                           "topic_id": tid, "error": repr(e)})
                else:
                    if self.verbose:
                        print(f"[agcl] retrieved topic {tid} "
                              f"(sim={sim:.2f}, seed={meta.get('seed_question','')!r}); "
                              f"skipping training")
                    _emit({"event": "retrieval_hit",
                           "topic_id": tid, "sim": sim,
                           "seed": meta.get("seed_question")})
                    self._on_topic_set(user_msg, tid)
                    return await self._first_turn_after_resolve(
                        user_msg, on_progress=on_progress,
                    )
            else:
                _emit({"event": "retrieval_miss",
                       "threshold": self.retrieval_threshold})

        return await self._bootstrap_and_train(user_msg, is_switch,
                                                on_progress=on_progress)

    async def _first_turn_after_resolve(self, user_msg: str,
                                         on_progress: Optional[Callable[[dict], None]] = None) -> str:
        """We just loaded a topic — answer locally, fall back to cloud if needed."""
        def _emit(ev: dict) -> None:
            if on_progress is not None:
                try:
                    on_progress(ev)
                except Exception:
                    pass

        _emit({"event": "generation_start", "mode": "local"})
        out = (self.mas.generate_text(
            user_msg, max_new_tokens=self.max_new_tokens,
        ) or "").strip()
        if _looks_degenerate(out):
            if self.verbose:
                print(f"[agcl] retrieved topic produced "
                      f"degenerate output; cloud fallback")
            _emit({"event": "fallback_to_cloud", "local_output": out})
            history = self.history + [{"role": "user", "content": user_msg}]
            out = await _cloud_full(history, self.provider)
        self.history = self.history + [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": out},
        ]
        _emit({"event": "generation_done", "mode": "local",
               "chars": len(out)})
        return out

    async def _bootstrap_and_train(self, user_msg: str,
                                   is_switch: bool,
                                   on_progress: Optional[Callable[[dict], None]] = None) -> str:
        def _emit(ev: dict) -> None:
            if on_progress is not None:
                try:
                    on_progress(ev)
                except Exception:
                    pass

        if self.verbose:
            tag = "topic switch — re" if is_switch else ""
            print(f"[agcl] {tag}bootstrapping training from cloud...")
        _emit({"event": "bootstrap_start", "is_switch": is_switch})

        answer, reforms = await bootstrap_pairs(
            user_msg,
            n_reformulations=self.n_reformulations,
            provider=self.provider,
        )
        _emit({"event": "bootstrap_done",
               "answer_chars": len(answer),
               "n_reformulations": len(reforms)})
        if self.verbose:
            print(f"[agcl] cloud answer: {len(answer)} chars, "
                  f"{len(reforms)} reformulations")
            print(f"[agcl] training links "
                  f"(stage A: {self.stage1_steps} steps, "
                  f"stage B: {self.stage2_steps} steps)...")

        self.control.reset()
        result = auto_train(
            self.mas, user_msg, answer, reforms,
            stage1_steps=self.stage1_steps,
            stage2_steps=self.stage2_steps,
            verbose=self.verbose,
            on_progress=on_progress,
            control=self.control,
        )
        if self.verbose:
            s1, s2 = result["stage1_losses"], result["stage2_losses"]
            if s1: print(f"[agcl] stage A: {s1[0]:.3f} -> {s1[-1]:.3f}")
            if s2: print(f"[agcl] stage B: {s2[0]:.3f} -> {s2[-1]:.3f}")

        # Persist trained links + planner-embedding centroid.
        tid = None
        if self.persist:
            with torch.no_grad():
                centroid = self._encode(user_msg).squeeze(0)
            try:
                tid = P.save_topic(
                    self.state_dir, self.mas, user_msg, centroid,
                    extra={"n_reformulations": len(reforms),
                           "stage1_final": (result["stage1_losses"] or [None])[-1],
                           "stage2_final": (result["stage2_losses"] or [None])[-1]},
                )
                if self.verbose:
                    print(f"[agcl] persisted topic {tid} -> "
                          f"{self.state_dir}/mas_topics/{tid}/")
                _emit({"event": "topic_persisted", "topic_id": tid})
            except Exception as e:
                if self.verbose:
                    print(f"[agcl] persist failed ({e!r}); continuing in-memory")
                _emit({"event": "topic_persist_failed", "error": repr(e)})

        self.history = [
            {"role": "user",      "content": user_msg},
            {"role": "assistant", "content": answer},
        ]
        self._on_topic_set(user_msg, tid)
        _emit({"event": "answer_ready", "chars": len(answer)})
        return answer

    def _on_topic_set(self, seed: str, topic_id: Optional[str]) -> None:
        self.trained_once = True
        self.topic_seed = seed
        self.current_topic_id = topic_id
        self.tracker.reset(seed)
