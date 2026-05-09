"""
Token / quota / provider tracker.

Records every cloud call AGCL makes — chat-path turns AND knowledge-path
bootstrap teacher calls — so a dashboard can show:

    - per-session token usage (chat vs knowledge formation, in + out)
    - per-provider running totals + remaining quota
    - per-provider reachability status
    - rolling timeseries for graphs

Three concerns under one roof:

    1. **Tracking**       record_call(...) appends to an in-memory log
                          + persists to <STATE_DIR>/usage.jsonl.
    2. **Quotas**         set_quota(provider, max_tokens|max_credit_usd)
                          checks happen before each call via check_quota().
    3. **Custom providers** users can register a custom OpenAI-compatible
                          provider (name, base_url, api_key_env, model).

Calls categorized by `kind`:
    - "chat"        the prefix-continuation path (user turn answers)
    - "knowledge"   bootstrap teacher answers + reformulations + topic
                    switch judgements (the cost of "knowledge formation")
    - "summarize"   context-window compression calls
    - "other"       any other cloud call

Persistence:
    <STATE_DIR>/usage.jsonl     append-only event log (one call = one line)
    <STATE_DIR>/usage_quotas.json   {provider: {max_tokens, max_credit_usd, period}}
    <STATE_DIR>/custom_providers.json   user-defined providers
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


_LOCK = threading.Lock()

# Singleton state
_STATE: Dict[str, Any] = {
    "calls":     [],   # in-memory ring of recent calls (last N)
    "quotas":    {},   # provider -> Quota
    "custom":    {},   # name -> CustomProvider dict
    "loaded":    False,
}

MAX_RING = 5000   # keep this many recent calls in RAM


# ----------------------------------------------------------------------
# Data shapes
# ----------------------------------------------------------------------

@dataclass
class Quota:
    """One provider's hard limit. Either tokens or credits, not both."""
    provider:        str
    max_tokens:      Optional[int] = None        # absolute cap on tokens (in+out)
    max_credit_usd:  Optional[float] = None      # absolute cap on cost
    period:          str = "lifetime"            # "lifetime" | "daily" | "monthly"
    notes:           str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"provider": self.provider, "max_tokens": self.max_tokens,
                "max_credit_usd": self.max_credit_usd, "period": self.period,
                "notes": self.notes}


# Token-based pricing (USD per 1k tokens) — best-effort defaults.
# Users can override via PRICING_<PROVIDER>_<MODEL>_INPUT / _OUTPUT env
# vars at any time. These are the defaults baked in for the dashboard's
# "estimated cost" column. They're conservative; we never claim they
# match billing — they're an estimate.
DEFAULT_PRICING: Dict[str, Dict[str, Dict[str, float]]] = {
    "claude": {
        "claude-sonnet-4-20250514": {"in": 0.003,  "out": 0.015},
        "claude-opus-4":            {"in": 0.015,  "out": 0.075},
        "claude-haiku-4-5":         {"in": 0.0008, "out": 0.004},
        "_default":                 {"in": 0.003,  "out": 0.015},
    },
    "openai": {
        "gpt-4o":      {"in": 0.0025, "out": 0.010},
        "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
        "_default":    {"in": 0.0025, "out": 0.010},
    },
    "_default": {"_default": {"in": 0.001, "out": 0.003}},
}


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------

def _state_dir() -> Path:
    from agcl import config as cfg
    p = Path(cfg.STATE_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _log_path() -> Path:        return _state_dir() / "usage.jsonl"
def _quotas_path() -> Path:     return _state_dir() / "usage_quotas.json"
def _custom_path() -> Path:     return _state_dir() / "custom_providers.json"


def _load_persistent() -> None:
    if _STATE["loaded"]:
        return
    _STATE["loaded"] = True
    qp = _quotas_path()
    if qp.exists():
        try:
            data = json.loads(qp.read_text())
            for prov, q in data.items():
                _STATE["quotas"][prov] = Quota(**q)
        except (json.JSONDecodeError, TypeError):
            pass
    cp = _custom_path()
    if cp.exists():
        try:
            _STATE["custom"] = json.loads(cp.read_text())
        except json.JSONDecodeError:
            pass
    # Replay last MAX_RING lines from usage.jsonl into the ring.
    lp = _log_path()
    if lp.exists():
        try:
            with open(lp) as f:
                lines = f.readlines()[-MAX_RING:]
            for line in lines:
                try:
                    _STATE["calls"].append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass


def _persist_quotas() -> None:
    data = {k: v.to_dict() for k, v in _STATE["quotas"].items()}
    _quotas_path().write_text(json.dumps(data, indent=2))


def _persist_custom() -> None:
    _custom_path().write_text(json.dumps(_STATE["custom"], indent=2))


# ----------------------------------------------------------------------
# Token estimation (fallback when SDK doesn't surface usage)
# ----------------------------------------------------------------------

def _count(text: str) -> int:
    """Reuse agcl.context.count_tokens — it already handles tiktoken fallback."""
    from agcl.context import count_tokens
    return count_tokens(text or "")


# ----------------------------------------------------------------------
# Pricing
# ----------------------------------------------------------------------

def _price(provider: str, model: str, in_tokens: int, out_tokens: int
            ) -> float:
    """Estimated USD cost. Reads env override if set, else built-in default."""
    env_key_in  = f"PRICING_{provider.upper()}_INPUT"
    env_key_out = f"PRICING_{provider.upper()}_OUTPUT"
    p_in  = os.getenv(env_key_in)
    p_out = os.getenv(env_key_out)
    if p_in is not None and p_out is not None:
        try:
            return (in_tokens / 1000) * float(p_in) + (out_tokens / 1000) * float(p_out)
        except ValueError:
            pass
    table = DEFAULT_PRICING.get(provider, DEFAULT_PRICING["_default"])
    rates = table.get(model) or table.get("_default") or {"in": 0.001, "out": 0.003}
    return (in_tokens / 1000) * rates["in"] + (out_tokens / 1000) * rates["out"]


# ----------------------------------------------------------------------
# Recording
# ----------------------------------------------------------------------

def record_call(*,
                 provider:    str,
                 model:       str,
                 kind:        str,            # "chat" | "knowledge" | "summarize" | "other"
                 session_id:  Optional[str],
                 in_tokens:   Optional[int] = None,
                 out_tokens:  Optional[int] = None,
                 in_text:     Optional[str] = None,
                 out_text:    Optional[str] = None,
                 latency_sec: Optional[float] = None,
                 ok:          bool = True,
                 error:       Optional[str] = None,
                 ) -> Dict[str, Any]:
    """
    Record one cloud call. If the SDK didn't surface in/out token counts,
    pass `in_text` / `out_text` and the tracker will estimate via tiktoken.
    """
    _load_persistent()
    if in_tokens is None:
        in_tokens = _count(in_text) if in_text else 0
    if out_tokens is None:
        out_tokens = _count(out_text) if out_text else 0
    cost_usd = _price(provider, model, in_tokens, out_tokens)
    entry: Dict[str, Any] = {
        "ts":         time.time(),
        "provider":   provider,
        "model":      model,
        "kind":       kind,
        "session_id": session_id or "default",
        "in_tokens":  int(in_tokens),
        "out_tokens": int(out_tokens),
        "total":      int(in_tokens + out_tokens),
        "cost_usd":   round(cost_usd, 6),
        "latency_sec": round(latency_sec, 3) if latency_sec is not None else None,
        "ok":         ok,
        "error":      error,
    }
    with _LOCK:
        _STATE["calls"].append(entry)
        if len(_STATE["calls"]) > MAX_RING:
            _STATE["calls"] = _STATE["calls"][-MAX_RING:]
        try:
            with open(_log_path(), "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass
    return entry


# ----------------------------------------------------------------------
# Quotas
# ----------------------------------------------------------------------

class QuotaExceeded(RuntimeError):
    """Raised by check_quota when a request would exceed the configured cap."""


def set_quota(provider: str, *,
               max_tokens: Optional[int] = None,
               max_credit_usd: Optional[float] = None,
               period: str = "lifetime",
               notes: str = "") -> Quota:
    _load_persistent()
    q = Quota(provider=provider, max_tokens=max_tokens,
              max_credit_usd=max_credit_usd, period=period, notes=notes)
    with _LOCK:
        _STATE["quotas"][provider] = q
        _persist_quotas()
    return q


def get_quota(provider: str) -> Optional[Quota]:
    _load_persistent()
    return _STATE["quotas"].get(provider)


def clear_quota(provider: str) -> bool:
    _load_persistent()
    with _LOCK:
        if provider in _STATE["quotas"]:
            del _STATE["quotas"][provider]
            _persist_quotas()
            return True
        return False


def all_quotas() -> Dict[str, Dict[str, Any]]:
    _load_persistent()
    return {k: v.to_dict() for k, v in _STATE["quotas"].items()}


def check_quota(provider: str) -> None:
    """
    Raise QuotaExceeded if the configured cap is already used up.
    Called BEFORE making the cloud call.
    """
    _load_persistent()
    q = _STATE["quotas"].get(provider)
    if q is None:
        return
    used = totals_for_period(provider, q.period)
    if q.max_tokens is not None and used["tokens"] >= q.max_tokens:
        raise QuotaExceeded(
            f"provider {provider!r} exceeded {q.period} token cap "
            f"({used['tokens']} >= {q.max_tokens})"
        )
    if q.max_credit_usd is not None and used["cost_usd"] >= q.max_credit_usd:
        raise QuotaExceeded(
            f"provider {provider!r} exceeded {q.period} cost cap "
            f"(${used['cost_usd']:.2f} >= ${q.max_credit_usd:.2f})"
        )


# ----------------------------------------------------------------------
# Aggregation queries (used by the dashboard endpoints)
# ----------------------------------------------------------------------

def _period_start(period: str) -> float:
    now = time.time()
    if period == "daily":
        # 00:00 local
        import datetime
        d = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return d.timestamp()
    if period == "monthly":
        import datetime
        d = datetime.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return d.timestamp()
    return 0.0   # lifetime


def totals_for_period(provider: str, period: str = "lifetime") -> Dict[str, Any]:
    _load_persistent()
    floor = _period_start(period)
    in_tokens = out_tokens = 0
    cost = 0.0
    n = 0
    for c in _STATE["calls"]:
        if c["provider"] != provider or c["ts"] < floor:
            continue
        in_tokens  += c["in_tokens"]
        out_tokens += c["out_tokens"]
        cost       += c["cost_usd"]
        n          += 1
    return {"in_tokens": in_tokens, "out_tokens": out_tokens,
            "tokens": in_tokens + out_tokens, "cost_usd": round(cost, 6),
            "calls": n, "period": period}


def session_breakdown(session_id: str) -> Dict[str, Any]:
    """Per-kind totals for one session."""
    _load_persistent()
    out: Dict[str, Dict[str, int]] = {}
    cost = 0.0
    for c in _STATE["calls"]:
        if c["session_id"] != session_id:
            continue
        k = c["kind"]
        bucket = out.setdefault(k, {"in_tokens": 0, "out_tokens": 0,
                                      "tokens": 0, "calls": 0,
                                      "cost_usd": 0.0})
        bucket["in_tokens"]  += c["in_tokens"]
        bucket["out_tokens"] += c["out_tokens"]
        bucket["tokens"]     += c["total"]
        bucket["calls"]      += 1
        bucket["cost_usd"]   = round(bucket["cost_usd"] + c["cost_usd"], 6)
        cost += c["cost_usd"]
    return {"session_id": session_id, "by_kind": out,
            "total_cost_usd": round(cost, 6)}


def all_sessions() -> List[Dict[str, Any]]:
    """One row per session: total tokens (chat + knowledge), cost, last seen."""
    _load_persistent()
    by_sid: Dict[str, Dict[str, Any]] = {}
    for c in _STATE["calls"]:
        sid = c["session_id"]
        row = by_sid.setdefault(sid, {
            "session_id": sid,
            "chat_tokens": 0, "knowledge_tokens": 0,
            "other_tokens": 0,
            "tokens": 0, "cost_usd": 0.0,
            "calls": 0, "last_ts": 0.0,
        })
        if   c["kind"] == "chat":      row["chat_tokens"]      += c["total"]
        elif c["kind"] == "knowledge": row["knowledge_tokens"] += c["total"]
        else:                           row["other_tokens"]    += c["total"]
        row["tokens"]   += c["total"]
        row["cost_usd"] = round(row["cost_usd"] + c["cost_usd"], 6)
        row["calls"]   += 1
        row["last_ts"]  = max(row["last_ts"], c["ts"])
    return sorted(by_sid.values(), key=lambda r: r["last_ts"], reverse=True)


def timeseries(*, session_id: Optional[str] = None,
                provider: Optional[str] = None,
                bucket_sec: int = 60,
                lookback_sec: int = 3600,
                kind: Optional[str] = None,
                ) -> List[Dict[str, Any]]:
    """
    Return [{ts, in_tokens, out_tokens, tokens, cost_usd}, ...] bucketed
    by `bucket_sec` over the last `lookback_sec`. Optional filters narrow
    by session, provider, kind.
    """
    _load_persistent()
    now = time.time()
    floor = now - lookback_sec
    buckets: Dict[int, Dict[str, Any]] = {}
    for c in _STATE["calls"]:
        if c["ts"] < floor:                          continue
        if session_id and c["session_id"] != session_id: continue
        if provider and c["provider"] != provider:    continue
        if kind and c["kind"] != kind:                continue
        b = int(c["ts"] // bucket_sec) * bucket_sec
        bk = buckets.setdefault(b, {
            "ts": float(b), "in_tokens": 0, "out_tokens": 0,
            "tokens": 0, "cost_usd": 0.0, "calls": 0,
        })
        bk["in_tokens"]  += c["in_tokens"]
        bk["out_tokens"] += c["out_tokens"]
        bk["tokens"]     += c["total"]
        bk["cost_usd"]    = round(bk["cost_usd"] + c["cost_usd"], 6)
        bk["calls"]      += 1
    return sorted(buckets.values(), key=lambda b: b["ts"])


def status_snapshot() -> Dict[str, Any]:
    """
    One blob the dashboard can render straight from. Combines:
        - per-provider running totals (lifetime, daily, monthly)
        - per-provider quotas + remaining
        - per-provider configured / reachable status
        - registered custom providers
    """
    _load_persistent()
    from agcl import config as cfg

    providers: Dict[str, Dict[str, Any]] = {
        "claude": {
            "configured": bool(cfg.ANTHROPIC_API_KEY),
            "model":      cfg.CLAUDE_MODEL,
            "default":    cfg.DEFAULT_CLOUD == "claude",
            "kind":       "builtin",
        },
        "openai": {
            "configured": bool(cfg.OPENAI_API_KEY),
            "model":      cfg.OPENAI_MODEL,
            "default":    cfg.DEFAULT_CLOUD == "openai",
            "kind":       "builtin",
        },
    }
    for name, spec in _STATE["custom"].items():
        providers[name] = {
            "configured": bool(os.getenv(spec.get("api_key_env", ""))),
            "model":      spec.get("model", ""),
            "base_url":   spec.get("base_url", ""),
            "kind":       "custom",
            "default":    False,
        }

    # toolkit gateways count too
    if os.getenv("AGCL_LLM_BASE_URL"):
        providers["litellm"] = {
            "configured": True,
            "model":      os.getenv("AGCL_LLM_MODEL", "smart"),
            "base_url":   os.environ["AGCL_LLM_BASE_URL"],
            "kind":       "gateway",
            "default":    False,
        }

    for prov, info in providers.items():
        info["lifetime"] = totals_for_period(prov, "lifetime")
        info["daily"]    = totals_for_period(prov, "daily")
        info["monthly"]  = totals_for_period(prov, "monthly")
        q = _STATE["quotas"].get(prov)
        if q is not None:
            used = totals_for_period(prov, q.period)
            info["quota"] = {
                **q.to_dict(),
                "used_tokens":     used["tokens"],
                "used_cost_usd":   used["cost_usd"],
                "remaining_tokens":
                    None if q.max_tokens is None else max(0, q.max_tokens - used["tokens"]),
                "remaining_credit_usd":
                    None if q.max_credit_usd is None else max(0.0, q.max_credit_usd - used["cost_usd"]),
            }

    return {
        "providers":     providers,
        "custom":        _STATE["custom"],
        "ring_count":    len(_STATE["calls"]),
        "ring_capacity": MAX_RING,
        "log_path":      str(_log_path()),
    }


# ----------------------------------------------------------------------
# Custom-provider registry
# ----------------------------------------------------------------------

def register_custom_provider(name: str, *,
                              base_url: str,
                              api_key_env: str,
                              model: str,
                              kind: str = "openai-compatible",
                              notes: str = "") -> Dict[str, Any]:
    """
    Add a user-defined OpenAI-compatible provider (e.g. Together, Groq,
    DeepInfra, a private vLLM). The gateway client picks it up via
    base_url; api_key is read from the named env var at call time.
    """
    if not name or not base_url:
        raise ValueError("name and base_url are required")
    _load_persistent()
    spec = {
        "name": name, "base_url": base_url.rstrip("/"),
        "api_key_env": api_key_env, "model": model,
        "kind": kind, "notes": notes,
    }
    with _LOCK:
        _STATE["custom"][name] = spec
        _persist_custom()
    return spec


def unregister_custom_provider(name: str) -> bool:
    _load_persistent()
    with _LOCK:
        if name in _STATE["custom"]:
            del _STATE["custom"][name]
            _persist_custom()
            return True
        return False


def list_custom_providers() -> List[Dict[str, Any]]:
    _load_persistent()
    return list(_STATE["custom"].values())


def custom_provider(name: str) -> Optional[Dict[str, Any]]:
    _load_persistent()
    return _STATE["custom"].get(name)
