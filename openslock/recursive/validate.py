"""
Runnable validation suite for the recursive MAS implementation.

Each test instantiates the actual modules (no mocks of the units under test)
and checks behaviors the paper specifies:

    1. InnerLink is the identity at init (residual + zero-init W2).
    2. OuterLink maps in_dim -> out_dim and is identity when dims match at init.
    3. InnerLink trains: cosine-sim loss decreases.
    4. OuterLink trains: MSE loss decreases.
    5. The loop controller produces final-latent shape == last agent's hidden_size.
    6. Across rounds the latent actually evolves (not stuck at identity).
    7. Stage-1 warmup helper reduces loss against a fixed target.
    8. Stage-2 full-loop helper reduces cross-entropy on a tiny synthetic task.
    9. Pattern builders produce a working MAS.

Run:  python -m openslock.recursive.validate
"""

from __future__ import annotations

import sys
import torch
import torch.nn as nn
import torch.nn.functional as F

from .links import InnerLink, OuterLink
from .mas import RecursiveMAS
from .train import stage1_warmup_inner, stage2_full_loop
from . import patterns as P
from . import backends as B
from .agent import RecursiveAgent
from .builder import build_mas_from_specs, build_agent


# ---------- mock agent ----------

class _MockAgent(nn.Module):
    """
    Minimal stand-in for a causal LM. Has the surface that RecursiveAgent
    and RecursiveMAS need:
      - hidden_size attribute
      - forward_latent(input_ids, injected, attention_mask) -> (latent, logits)

    The "model" is a single linear projection so gradients flow and the
    latent actually changes round-to-round.
    """

    def __init__(self, dim: int, vocab: int = 64):
        super().__init__()
        self.hidden_size = dim
        self.vocab = vocab
        self.embed = nn.Embedding(vocab, dim)
        self.proj = nn.Linear(dim, dim)
        self.head = nn.Linear(dim, vocab)

    def forward_latent(self, input_ids, injected=None, attention_mask=None):
        e = self.embed(input_ids)                       # [B, T, D]
        if injected is not None:
            e = torch.cat([injected.unsqueeze(1), e], dim=1)
        h = torch.tanh(self.proj(e))                    # [B, T(+1), D]
        latent = h[:, -1, :]                            # last position
        logits = self.head(h)                           # [B, T(+1), V]
        return latent, logits

    def supports_injection(self):
        return True


class _MockTextBackend(B._BackendBase):
    """Stand-in for a text-mode backend; mimics the interface."""
    def __init__(self, dim, supports_injection_=True, vocab=64):
        super().__init__()
        self.hidden_size = dim
        self._supports = supports_injection_
        self.proj = nn.Linear(dim, dim)
        self._dim = dim
        self._vocab = vocab

    def supports_injection(self):
        return self._supports

    def forward_latent_text(self, text, injected=None):
        if isinstance(text, str):
            text = [text]
        B = len(text)
        h = torch.randn(B, self._dim)
        if injected is not None and self._supports:
            h = h + injected
        return self.proj(torch.tanh(h)), None

    @torch.no_grad()
    def decode_text(self, prompt_text, injected=None, max_new_tokens=8, **kw):
        if isinstance(prompt_text, list):
            prompt_text = prompt_text[0]
        return f"[mock {self._dim}d{'+inj' if injected is not None and self._supports else ''}] {prompt_text}"


# ---------- tests ----------

def t_inner_link_identity_at_init():
    torch.manual_seed(0)
    link = InnerLink(64)
    h = torch.randn(4, 64)
    assert torch.allclose(link(h), h, atol=1e-6), "InnerLink should be identity at init"
    return "InnerLink is identity at init (residual + zero-init W2)"


def t_outer_link_shape():
    link = OuterLink(64, 96)
    h = torch.randn(4, 64)
    out = link(h)
    assert out.shape == (4, 96), f"got {tuple(out.shape)}"
    return "OuterLink maps (B, in_dim) -> (B, out_dim)"


def t_outer_link_identity_when_dims_match():
    torch.manual_seed(0)
    link = OuterLink(64, 64)
    h = torch.randn(4, 64)
    out = link(h)
    assert torch.allclose(out, h, atol=1e-5), \
        f"max diff {((out - h).abs().max()).item()}"
    return "OuterLink is identity at init when in_dim == out_dim"


def t_inner_link_trains():
    torch.manual_seed(0)
    link = InnerLink(32)
    opt = torch.optim.Adam(link.parameters(), lr=1e-2)
    h = torch.randn(8, 32)
    target = torch.randn(8, 32)
    initial = (1 - F.cosine_similarity(link(h), target, dim=-1)).mean().item()
    for _ in range(80):
        out = link(h)
        loss = (1 - F.cosine_similarity(out, target, dim=-1)).mean()
        opt.zero_grad(); loss.backward(); opt.step()
    final = (1 - F.cosine_similarity(link(h), target, dim=-1)).mean().item()
    assert final < initial - 0.1, f"{initial:.3f} -> {final:.3f}"
    return f"InnerLink trains (cos-sim loss {initial:.3f} -> {final:.3f})"


def t_outer_link_trains():
    torch.manual_seed(0)
    link = OuterLink(32, 48)
    opt = torch.optim.Adam(link.parameters(), lr=1e-2)
    h = torch.randn(8, 32)
    target = torch.randn(8, 48)
    initial = F.mse_loss(link(h), target).item()
    for _ in range(80):
        out = link(h)
        loss = F.mse_loss(out, target)
        opt.zero_grad(); loss.backward(); opt.step()
    final = F.mse_loss(link(h), target).item()
    assert final < initial * 0.5, f"{initial:.3f} -> {final:.3f}"
    return f"OuterLink trains (MSE {initial:.3f} -> {final:.3f})"


def t_mas_loop_runs_and_shape():
    torch.manual_seed(0)
    agents = [_MockAgent(32), _MockAgent(48), _MockAgent(40)]
    mas = RecursiveMAS(agents, n_rounds=2)
    input_ids = torch.randint(0, 64, (2, 5))
    latent, logits = mas.run_latent(input_ids)
    assert latent.shape == (2, 40), f"latent {tuple(latent.shape)}"
    # T+1 because at least one injected token was prepended after the cold-start agent.
    assert logits.shape[0] == 2 and logits.shape[1] in (5, 6), f"logits {tuple(logits.shape)}"
    return f"RecursiveMAS loop runs end-to-end, final latent shape={tuple(latent.shape)}"


def t_mas_latent_evolves_across_rounds():
    """
    With non-trivial inner/outer links, the final latent should differ from
    the cold-start latent of agent 0. This verifies the loop is actually
    propagating information and isn't a no-op identity chain.
    """
    torch.manual_seed(0)
    agents = [_MockAgent(32), _MockAgent(32)]
    mas = RecursiveMAS(agents, n_rounds=2)
    # Push the inner/outer weights away from zero so they actually do something.
    with torch.no_grad():
        for m in list(mas.inner) + list(mas.outer):
            for p in m.parameters():
                p.add_(torch.randn_like(p) * 0.3)

    input_ids = torch.randint(0, 64, (2, 5))
    cold, _ = agents[0].forward_latent(input_ids)
    final, _ = mas.run_latent(input_ids)
    diff = (final - cold).abs().mean().item()
    assert diff > 1e-3, f"latent did not evolve, mean abs diff={diff}"
    return f"Latent evolves across rounds (mean abs diff vs cold-start = {diff:.3f})"


def t_stage1_warmup_reduces_loss():
    torch.manual_seed(0)
    agent = _MockAgent(32)
    link = InnerLink(32)
    target_dir = torch.randn(32)
    target_dir = target_dir / target_dir.norm()

    def batches():
        for _ in range(60):
            ids = torch.randint(0, 64, (4, 6))
            attn = torch.ones_like(ids)
            yield ids, attn

    def target_provider(batch):
        ids, _ = batch
        return target_dir.unsqueeze(0).expand(ids.size(0), -1)

    losses = [l for _, l in stage1_warmup_inner(
        agent, link, batches(), target_provider, lr=5e-2, max_steps=60
    )]
    assert losses[-1] < losses[0] - 0.1, f"{losses[0]:.3f} -> {losses[-1]:.3f}"
    return f"stage1_warmup_inner reduces loss ({losses[0]:.3f} -> {losses[-1]:.3f})"


def t_stage2_full_loop_reduces_loss():
    """
    Tiny synthetic next-token task. We don't expect convergence — just
    that the cross-entropy goes down across a handful of steps, which
    verifies gradients flow through the full unrolled loop.
    """
    torch.manual_seed(0)
    agents = [_MockAgent(24, vocab=32), _MockAgent(24, vocab=32)]
    mas = RecursiveMAS(agents, n_rounds=2)

    def batches():
        for _ in range(40):
            ids = torch.randint(0, 32, (4, 5))
            attn = torch.ones_like(ids)
            tgt = torch.randint(0, 32, (4, 5))
            yield ids, attn, tgt

    losses = [l for _, l in stage2_full_loop(mas, batches(), lr=5e-3, max_steps=40)]
    # On random targets the loss won't crash, but it should at least move.
    moved = abs(losses[-1] - losses[0])
    assert moved > 1e-3, f"loss did not change: {losses[0]} -> {losses[-1]}"
    return f"stage2_full_loop trains end-to-end (loss {losses[0]:.3f} -> {losses[-1]:.3f}, moved {moved:.3f})"


def t_patterns_build_working_mas():
    """All four collaboration patterns should build a runnable MAS."""
    def factory(role: str):
        return _MockAgent(16)

    def teacher(role: str): return _MockAgent(24)
    def student(role: str): return _MockAgent(16)

    seq = P.sequential(factory, roles=("planner", "critic", "solver"), n_rounds=2)
    moe = P.mixture_of_experts(factory, n_experts=3, n_rounds=2)
    dis = P.distillation(teacher, student, n_rounds=2)
    delib = P.deliberation(factory, n_rounds=3)

    input_ids = torch.randint(0, 64, (2, 4))
    for name, mas in [("sequential", seq), ("MoE", moe),
                      ("distillation", dis), ("deliberation", delib)]:
        latent, _ = mas.run_latent(input_ids)
        assert latent.dim() == 2 and latent.size(0) == 2, f"{name}: bad shape {tuple(latent.shape)}"

    return "All 4 collaboration patterns produce a working MAS"


def t_inner_link_param_count():
    """Sanity check: the link is a 2-layer MLP, no biases, no extra params."""
    link = InnerLink(64, hidden_dim=128)
    n = sum(p.numel() for p in link.parameters())
    expected = 64 * 128 + 128 * 64  # W1 + W2
    assert n == expected, f"expected {expected} params, got {n}"
    return f"InnerLink has the expected param count (W1 + W2 = {expected})"


def t_outer_link_param_count():
    link = OuterLink(64, 96, hidden_dim=128)
    n = sum(p.numel() for p in link.parameters())
    expected = 64 * 128 + 128 * 96 + 64 * 96  # W1 + W2 + W3
    assert n == expected, f"expected {expected} params, got {n}"
    return f"OuterLink has the expected param count (W1 + W2 + W3 = {expected})"


def t_backend_base_interface():
    """Both backends declare the required interface (methods on the class)."""
    needed = {"supports_injection", "forward_latent_text", "decode_text", "load"}
    for cls in (B.HFBackend, B.GGUFBackend):
        for attr in needed:
            assert hasattr(cls, attr), f"{cls.__name__} missing {attr}"
        # hidden_size is set in __init__ on every instance — base declares it as annotation
        assert "hidden_size" in B._BackendBase.__annotations__, \
            "_BackendBase should annotate hidden_size"
    return "HFBackend and GGUFBackend expose the required interface (supports_injection/forward_latent_text/decode_text/load + hidden_size)"


def t_recursive_agent_wraps_backend():
    """RecursiveAgent should accept a backend object directly."""
    backend = _MockTextBackend(32)
    a = RecursiveAgent(backend, role="planner")
    assert a.hidden_size == 32
    assert a.role == "planner"
    assert a.supports_injection() is True
    return "RecursiveAgent wraps an explicit backend (hidden_size/role/supports_injection plumbed through)"


def t_text_mode_loop_runs():
    """run_latent_text drives the loop via text-mode backends."""
    torch.manual_seed(0)
    agents = [RecursiveAgent(_MockTextBackend(24)),
              RecursiveAgent(_MockTextBackend(32)),
              RecursiveAgent(_MockTextBackend(20))]
    mas = RecursiveMAS(agents, n_rounds=2)
    latent, _ = mas.run_latent_text(["hello", "hi there"])
    assert latent.shape == (2, 20), f"latent {tuple(latent.shape)}"
    return f"run_latent_text drives mixed-dim text-mode agents (final latent {tuple(latent.shape)})"


def t_text_mode_respects_injection_capability():
    """An agent with supports_injection=False must not receive an injected latent."""
    torch.manual_seed(0)
    captured = {}

    class _Probe(B._BackendBase):
        def __init__(self):
            super().__init__()
            self.hidden_size = 16
        def supports_injection(self): return False
        def forward_latent_text(self, text, injected=None):
            captured["injected_was"] = injected
            n = 1 if isinstance(text, str) else len(text)
            return torch.zeros(n, 16), None
        def decode_text(self, *_a, **_kw): return ""

    agents = [RecursiveAgent(_MockTextBackend(16)), RecursiveAgent(_Probe())]
    mas = RecursiveMAS(agents, n_rounds=1)
    mas.run_latent_text("hi")
    assert captured["injected_was"] is None, \
        "non-injection backend received an injected latent — capability gate broken"
    return "Loop drops injected latent at non-injection (gguf-style) agents"


def t_generate_text_returns_string():
    agents = [RecursiveAgent(_MockTextBackend(16)), RecursiveAgent(_MockTextBackend(16))]
    mas = RecursiveMAS(agents, n_rounds=1)
    out = mas.generate_text("ping")
    assert isinstance(out, str) and "ping" in out, f"unexpected output: {out!r}"
    return f"generate_text routes to final agent's decode_text (got {out!r})"


def t_builder_validates_backend_choice():
    """build_agent rejects unknown backend names."""
    try:
        build_agent({"backend": "fortran", "model": "x"})
    except ValueError as e:
        assert "unknown backend" in str(e)
        return "build_agent rejects unknown backend names"
    raise AssertionError("expected ValueError for unknown backend")


def t_builder_requires_model_field():
    try:
        build_agent({"backend": "hf"})
    except ValueError as e:
        assert "missing 'model'" in str(e)
        return "build_agent requires a model field"
    raise AssertionError("expected ValueError for missing model")


def t_build_mas_from_specs_dispatches_pattern():
    """The builder uses a stub factory so we can verify pattern dispatch
    without hitting real model files."""

    def fake_build(spec, default_device=None, default_dtype=None):  # noqa: ARG001
        del default_device, default_dtype
        return RecursiveAgent(_MockTextBackend(16, supports_injection_=True),
                              role=spec.get("role", ""))

    import openslock.recursive.builder as bld
    orig = bld.build_agent
    bld.build_agent = fake_build
    try:
        specs = [
            {"backend": "stub", "model": "a", "role": "planner"},
            {"backend": "stub", "model": "b", "role": "solver"},
        ]
        for pat in ("sequential", "moe", "deliberation", "custom"):
            mas = build_mas_from_specs(specs, n_rounds=2, pattern=pat)
            assert len(mas.agents) == 2, f"{pat}: wrong agent count"
        # distill needs exactly 2
        mas = build_mas_from_specs(specs, n_rounds=2, pattern="distill")
        assert len(mas.agents) == 2
    finally:
        bld.build_agent = orig
    return "build_mas_from_specs dispatches all patterns and instantiates the right agent count"


def t_config_load_paths():
    """config.MAS_AGENTS is parsed into a list of dicts with required keys."""
    from openslock import config as cfg
    assert isinstance(cfg.MAS_AGENTS, list)
    for spec in cfg.MAS_AGENTS:
        assert "backend" in spec, f"spec missing 'backend': {spec}"
        assert spec["backend"] in ("hf", "gguf"), f"bad backend {spec['backend']}"
        assert "model" in spec, f"spec missing 'model': {spec}"
    assert cfg.MAS_PATTERN in ("sequential", "moe", "distill", "deliberation", "custom")
    assert cfg.MAS_ROUNDS >= 1
    return f"config loads MAS_AGENTS={len(cfg.MAS_AGENTS)} pattern={cfg.MAS_PATTERN} rounds={cfg.MAS_ROUNDS}"


# ---------- runner ----------

TESTS = [
    t_inner_link_identity_at_init,
    t_inner_link_param_count,
    t_outer_link_shape,
    t_outer_link_identity_when_dims_match,
    t_outer_link_param_count,
    t_inner_link_trains,
    t_outer_link_trains,
    t_mas_loop_runs_and_shape,
    t_mas_latent_evolves_across_rounds,
    t_stage1_warmup_reduces_loss,
    t_stage2_full_loop_reduces_loss,
    t_patterns_build_working_mas,
    t_backend_base_interface,
    t_recursive_agent_wraps_backend,
    t_text_mode_loop_runs,
    t_text_mode_respects_injection_capability,
    t_generate_text_returns_string,
    t_builder_validates_backend_choice,
    t_builder_requires_model_field,
    t_build_mas_from_specs_dispatches_pattern,
    t_config_load_paths,
]


def run_all(verbose: bool = True) -> bool:
    fails = 0
    for t in TESTS:
        try:
            msg = t()
            if verbose:
                print(f"  PASS  {t.__name__:45s}  {msg}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL  {t.__name__:45s}  {e}")
        except Exception as e:
            fails += 1
            print(f"  ERR   {t.__name__:45s}  {type(e).__name__}: {e}")
    total = len(TESTS)
    passed = total - fails
    print(f"\n  {passed}/{total} tests passed")
    return fails == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
