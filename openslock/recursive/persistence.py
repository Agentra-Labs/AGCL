"""
Persistence + similarity retrieval for trained-per-topic RecursiveMAS state.

Each topic is one directory:

    STATE_DIR/mas_topics/<topic_id>/
        meta.json          # seed_question, role/dim signature, n_turns, timestamps
        centroid.pt        # 1-D float32 tensor — planner's encoding of seed
        links.pt           # state_dict of mas.inner + mas.outer (link weights only)

The directory layout is flat and append-only — one folder per learned
topic. Lookup is done by loading every centroid into a single (N, D)
numpy matrix and taking cosine similarity against the query embedding.
For our use case N stays small (one entry per topic the user has ever
opened), so this is fine; if N grows, swap in FAISS without changing the
index API.

The link state is keyed under "inner.<i>.<param>" / "outer.<i>.<param>"
to match `mas.inner` / `mas.outer` ModuleList layouts. We refuse to load
into a MAS whose dim signature (per-agent hidden_size + role tuple)
doesn't match the saved one, since OuterLink dims are baked in.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import torch


# ---------- low-level helpers ----------

def _topic_id(seed_question: str) -> str:
    """Stable 12-char id derived from the seed question."""
    h = hashlib.sha1(seed_question.strip().encode("utf-8")).hexdigest()
    return h[:12]


def _topics_dir(state_dir: str) -> Path:
    p = Path(state_dir) / "mas_topics"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _signature(mas) -> Dict[str, Any]:
    """Captures the shape contract the saved links assume."""
    return {
        "dims":  [int(a.hidden_size) for a in mas.agents],
        "roles": [str(getattr(a, "role", "") or "") for a in mas.agents],
        "n_rounds": int(mas.n_rounds),
    }


def _link_state(mas) -> Dict[str, torch.Tensor]:
    out: Dict[str, torch.Tensor] = {}
    for i, m in enumerate(mas.inner):
        for k, v in m.state_dict().items():
            out[f"inner.{i}.{k}"] = v.detach().cpu()
    for i, m in enumerate(mas.outer):
        for k, v in m.state_dict().items():
            out[f"outer.{i}.{k}"] = v.detach().cpu()
    return out


def _load_link_state(mas, state: Dict[str, torch.Tensor]) -> None:
    for i, m in enumerate(mas.inner):
        sd = {k.split(".", 2)[2]: v
              for k, v in state.items() if k.startswith(f"inner.{i}.")}
        m.load_state_dict(sd, strict=True)
    for i, m in enumerate(mas.outer):
        sd = {k.split(".", 2)[2]: v
              for k, v in state.items() if k.startswith(f"outer.{i}.")}
        m.load_state_dict(sd, strict=True)


# ---------- save / load one topic ----------

def save_topic(state_dir: str, mas, seed_question: str,
               centroid: torch.Tensor,
               extra: Optional[Dict[str, Any]] = None) -> str:
    """
    Persist a topic's trained links + centroid + metadata. Returns the topic id.
    Idempotent: same seed_question overwrites the same directory.
    """
    tid = _topic_id(seed_question)
    d = _topics_dir(state_dir) / tid
    d.mkdir(parents=True, exist_ok=True)

    sig = _signature(mas)
    meta = {
        "topic_id":      tid,
        "seed_question": seed_question,
        "signature":     sig,
        "saved_at":      time.time(),
        "centroid_dim":  int(centroid.numel()),
    }
    if extra:
        meta.update(extra)

    torch.save(_link_state(mas), d / "links.pt")
    torch.save(centroid.detach().cpu().float(), d / "centroid.pt")
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    return tid


def load_topic(state_dir: str, mas, topic_id: str,
               strict_signature: bool = True) -> Dict[str, Any]:
    """
    Load saved links into `mas` in place. Returns the topic's metadata.
    Raises ValueError on signature mismatch (unless strict_signature=False).
    """
    d = _topics_dir(state_dir) / topic_id
    if not (d / "meta.json").exists():
        raise FileNotFoundError(f"no topic {topic_id} under {d}")
    meta = json.loads((d / "meta.json").read_text())

    if strict_signature:
        cur = _signature(mas)
        saved = meta.get("signature", {})
        if cur.get("dims") != saved.get("dims"):
            raise ValueError(
                f"dim signature mismatch: saved {saved.get('dims')} "
                f"vs current {cur.get('dims')} — links would not fit."
            )

    state = torch.load(d / "links.pt", map_location="cpu", weights_only=True)
    _load_link_state(mas, state)
    return meta


# ---------- index ----------

def list_topics(state_dir: str) -> List[Dict[str, Any]]:
    """Return metadata for every saved topic, newest first."""
    base = _topics_dir(state_dir)
    out: List[Dict[str, Any]] = []
    for d in base.iterdir():
        if not d.is_dir(): continue
        m = d / "meta.json"
        if not m.exists(): continue
        try:
            out.append(json.loads(m.read_text()))
        except Exception:
            continue
    out.sort(key=lambda r: r.get("saved_at", 0), reverse=True)
    return out


def _load_centroid(state_dir: str, topic_id: str) -> Optional[torch.Tensor]:
    p = _topics_dir(state_dir) / topic_id / "centroid.pt"
    if not p.exists():
        return None
    try:
        return torch.load(p, map_location="cpu", weights_only=True).float()
    except Exception:
        return None


def find_similar_topic(state_dir: str, query: torch.Tensor,
                        threshold: float = 0.75,
                        signature: Optional[Dict[str, Any]] = None
                        ) -> Optional[Tuple[str, float, Dict[str, Any]]]:
    """
    Cosine-sim lookup. Skips topics whose signature doesn't match (so we
    never try to load 896-dim links into a 1024-dim MAS).

    Returns (topic_id, similarity, meta) for the best match >= threshold,
    or None.
    """
    q = query.detach().cpu().float().flatten()
    qn = q / (q.norm() + 1e-8)

    best: Optional[Tuple[str, float, Dict[str, Any]]] = None
    for meta in list_topics(state_dir):
        if signature is not None and meta.get("signature", {}).get("dims") != signature.get("dims"):
            continue
        c = _load_centroid(state_dir, meta["topic_id"])
        if c is None or c.numel() != q.numel():
            continue
        cn = c.flatten()
        cn = cn / (cn.norm() + 1e-8)
        sim = float(torch.dot(qn, cn).item())
        if best is None or sim > best[1]:
            best = (meta["topic_id"], sim, meta)
    if best is None or best[1] < threshold:
        return None
    return best


def delete_topic(state_dir: str, topic_id: str) -> bool:
    d = _topics_dir(state_dir) / topic_id
    if not d.exists():
        return False
    for child in d.iterdir():
        try:
            child.unlink()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        return False
    return True
