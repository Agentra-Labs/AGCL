"""
Training-strategy presets for the mini trainer.

A "strategy" is a list of (loss_name, weight) pairs. The trainer
iterates the list each step, computes each loss against whatever
fields the batch happens to have, and weighted-sums them. Missing
fields (e.g. a sample with no captured latent) are silently skipped so
the trainer keeps making progress on heterogeneous data.

Available losses:
    ce              cross-entropy on output token sequence
    distill_latent  cosine distance between student pooled hidden
                    (projected) and the captured MAS latent
    kl              KL(student || teacher) on logits when teacher
                    logits are present
    contrastive     InfoNCE between positive (prompt, output) pairs
                    inside the batch
    reform_ce       cross-entropy on cloud-reformulation tokens
                    (treats reformulations as additional CE targets)

Presets:
    ce_only           [(ce, 1.0)]
    latent_only       [(distill_latent, 1.0)]
    ce_plus_latent    [(ce, 1.0), (distill_latent, 0.5)]
    full              [(ce, 1.0), (distill_latent, 0.5), (reform_ce, 0.3)]
    kl_distill        [(kl, 1.0), (ce, 0.2)]
    contrastive_only  [(contrastive, 1.0)]
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import torch
import torch.nn.functional as F


# --- individual loss components --------------------------------------

def loss_ce(model, batch) -> torch.Tensor:
    """Cross-entropy on the output token sequence."""
    ids_in  = batch.get("input_ids")
    ids_tgt = batch.get("target_ids")
    if ids_in is None or ids_tgt is None:
        return torch.zeros((), device=_dev(model))
    logits = model(ids_in)
    # Shift target so position t predicts target[t]; align lengths.
    T = min(logits.size(1), ids_tgt.size(1))
    return F.cross_entropy(
        logits[:, :T, :].reshape(-1, logits.size(-1)),
        ids_tgt[:, :T].reshape(-1),
        ignore_index=-100,
    )


def loss_reform_ce(model, batch) -> torch.Tensor:
    """CE on reformulation token sequences (when present)."""
    refs = batch.get("reform_ids")
    if not refs:
        return torch.zeros((), device=_dev(model))
    losses = []
    for r_in, r_tgt in refs:
        logits = model(r_in)
        T = min(logits.size(1), r_tgt.size(1))
        losses.append(F.cross_entropy(
            logits[:, :T, :].reshape(-1, logits.size(-1)),
            r_tgt[:, :T].reshape(-1),
            ignore_index=-100,
        ))
    return torch.stack(losses).mean()


def loss_distill_latent(model, batch) -> torch.Tensor:
    """Cosine distance between student pooled hidden (projected) and MAS latent."""
    ids = batch.get("input_ids")
    lat = batch.get("latent")
    if ids is None or lat is None:
        return torch.zeros((), device=_dev(model))
    h = model.pooled_hidden(ids)                   # [B, H]
    if h.size(0) != lat.size(0):
        # Broadcast a single latent over the batch.
        lat = lat.expand(h.size(0), -1)
    proj = model.project_to_latent(h, lat.size(-1))
    return (1.0 - F.cosine_similarity(proj, lat, dim=-1)).mean()


def loss_kl(model, batch) -> torch.Tensor:
    """KL(student || teacher) when teacher_logits provided."""
    ids = batch.get("input_ids")
    teacher = batch.get("teacher_logits")
    if ids is None or teacher is None:
        return torch.zeros((), device=_dev(model))
    student = model(ids)
    T = min(student.size(1), teacher.size(1))
    p = F.log_softmax(student[:, :T, :], dim=-1)
    q = F.softmax(teacher[:, :T, :], dim=-1)
    return F.kl_div(p, q, reduction="batchmean")


def loss_contrastive(model, batch) -> torch.Tensor:
    """InfoNCE on (input, target) pairs in the batch."""
    ids_in  = batch.get("input_ids")
    ids_tgt = batch.get("target_ids")
    if ids_in is None or ids_tgt is None or ids_in.size(0) < 2:
        return torch.zeros((), device=_dev(model))
    ha = model.pooled_hidden(ids_in)
    hb = model.pooled_hidden(ids_tgt)
    ha = F.normalize(ha, dim=-1)
    hb = F.normalize(hb, dim=-1)
    logits = ha @ hb.t() / 0.07           # temperature
    targets = torch.arange(ha.size(0), device=ha.device)
    return F.cross_entropy(logits, targets)


def _dev(model) -> torch.device:
    p = next(model.parameters(), None)
    return p.device if p is not None else torch.device("cpu")


# --- registry --------------------------------------------------------

LOSSES = {
    "ce":              loss_ce,
    "distill_latent":  loss_distill_latent,
    "kl":              loss_kl,
    "contrastive":     loss_contrastive,
    "reform_ce":       loss_reform_ce,
}

PRESETS: Dict[str, List[Tuple[str, float]]] = {
    "ce_only":          [("ce", 1.0)],
    "latent_only":      [("distill_latent", 1.0)],
    "ce_plus_latent":   [("ce", 1.0), ("distill_latent", 0.5)],
    "full":             [("ce", 1.0), ("distill_latent", 0.5), ("reform_ce", 0.3)],
    "kl_distill":       [("kl", 1.0), ("ce", 0.2)],
    "contrastive_only": [("contrastive", 1.0)],
}


def compose_loss(model, batch, strategy: str) -> torch.Tensor:
    """Return the weighted sum of losses defined by `strategy`."""
    components = PRESETS.get(strategy, PRESETS["ce_plus_latent"])
    total = torch.zeros((), device=_dev(model))
    for name, weight in components:
        fn = LOSSES.get(name)
        if fn is None:
            continue
        try:
            v = fn(model, batch)
        except Exception:
            continue
        total = total + weight * v
    return total
