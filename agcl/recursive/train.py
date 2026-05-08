"""
Two-stage training from the paper.

Stage 1 — inner-loop warmup
    Train each agent's InnerLink in isolation to align its latent thought
    with a target embedding. Loss = 1 - cos_sim(produced, target).
    This stabilizes the latent space before the full loop is unrolled.

Stage 2 — full-loop training
    Unroll the whole MAS for n_rounds. Backprop cross-entropy on the final
    decoded logits against the ground-truth answer. Every agent and every
    link receives gradient because the loop is fully differentiable.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


def stage1_warmup_inner(agent, inner_link, batches, target_provider,
                        lr: float = 1e-3, max_steps: int | None = None,
                        freeze_agent: bool = True):
    """
    Yields (step, loss_scalar) per training step so the caller can log.

    Args:
        agent: a RecursiveAgent (or anything implementing forward_latent)
        inner_link: the InnerLink to train (typically agent's slot in mas.inner)
        batches: iterable of (input_ids, attention_mask)
        target_provider: callable(batch) -> [B, D] target latent
        lr: learning rate for the InnerLink
        max_steps: stop after this many steps; None = exhaust batches
        freeze_agent: if True, only InnerLink params get gradient (the standard
                      paper warm-up; the underlying LM is held fixed).
    """
    params = list(inner_link.parameters())
    if not freeze_agent:
        params += list(agent.parameters())
    opt = torch.optim.AdamW(params, lr=lr)

    for step, batch in enumerate(batches):
        if max_steps is not None and step >= max_steps:
            break
        input_ids, attn = batch
        if freeze_agent:
            with torch.no_grad():
                h, _ = agent.forward_latent(input_ids, attention_mask=attn)
        else:
            h, _ = agent.forward_latent(input_ids, attention_mask=attn)

        h_in = inner_link(h)
        target = target_provider(batch).to(h_in.dtype).to(h_in.device)
        loss = (1.0 - F.cosine_similarity(h_in, target, dim=-1)).mean()

        opt.zero_grad()
        loss.backward()
        opt.step()
        yield step, float(loss.detach().item())


def stage2_full_loop(mas, batches, lr: float = 1e-4,
                    max_steps: int | None = None,
                    pad_token_id: int = -100):
    """
    Trains the full unrolled loop end-to-end with cross-entropy.

    Args:
        mas: a RecursiveMAS
        batches: iterable of (input_ids, attention_mask, target_ids)
                 target_ids align to the last positions of the final logits.
        lr: learning rate for ALL trainable parameters in the MAS
        max_steps: optional step cap
        pad_token_id: positions in target_ids equal to this are ignored
                      in the cross-entropy loss.
    """
    opt = torch.optim.AdamW(mas.parameters(), lr=lr)

    for step, batch in enumerate(batches):
        if max_steps is not None and step >= max_steps:
            break
        input_ids, attn, target_ids = batch

        _, logits = mas.run_latent(input_ids, attention_mask=attn)

        T_target = target_ids.size(1)
        # Final logits are over (T+1) positions (with injected latent); take
        # the last T_target positions to align with target tokens.
        logits_use = logits[:, -T_target:, :]
        loss = F.cross_entropy(
            logits_use.reshape(-1, logits_use.size(-1)),
            target_ids.reshape(-1),
            ignore_index=pad_token_id,
        )

        opt.zero_grad()
        loss.backward()
        opt.step()
        yield step, float(loss.detach().item())
