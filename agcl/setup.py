"""
agcl.setup — auto-setup wizards and model downloaders.

Wraps the existing CLI pieces (autoconfig.run, configurator helpers, the
HF + GGUF download paths) behind a small in-memory job manager so the
HTTP API can stream progress while a download runs in a background
thread. Same functions are also called directly by the CLI subcommands
in main.py — no business logic is duplicated.

What's here:

  - Job manager: lightweight thread-based jobs that publish events.
    Each Job has a state (pending|running|done|error|cancelled),
    a progress percentage 0..100, and an append-only event log.

  - download_hf_snapshot(repo_id, dest)
    Wraps huggingface_hub.snapshot_download. Skips TF / Flax weight
    files. Default destination: models/hf_local/<slugified_repo>/.

  - download_gguf(source, filename, dest)
    `source` may be a https://… URL or an "owner/repo" pair (HF Hub).
    Default destination: models/.

  - auto_recursive(force, install_deps, download)
    Wraps agcl.autoconfig.run with stdout capture so events surface in
    the job log instead of just the terminal.

  - auto_chat(local_model_path, default_cloud, force)
    Patches .env with LOCAL_MODEL_PATH and DEFAULT_CLOUD if provided.
    Reports which keys actually changed.

  - list_local_models()
    Scans the conventional model dirs (models/, models/hf/,
    models/hf_local/, ~/.cache/huggingface/hub/) and reports cached
    GGUF files + HF model snapshots.

  - model_registry()
    Curated list of recommended models for the GUI quick-pick:
    small GGUF chat heads + the canonical HF MAS pair.

The job manager is intentionally simple — fine for a single-user node;
not a substitute for Celery/RQ if you need persistence or fan-out.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import threading
import time
import urllib.request
import uuid
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"
HF_LOCAL_DIR = MODELS_DIR / "hf_local"
HF_CACHE_DIR = MODELS_DIR / "hf"


# ============================================================
# Job manager
# ============================================================

@dataclass
class Event:
    ts: float
    level: str        # info | warn | error
    message: str
    progress: Optional[float] = None    # 0..100, optional
    data: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("data") is None: d.pop("data")
        if d.get("progress") is None: d.pop("progress")
        return d


@dataclass
class Job:
    id: str
    kind: str
    state: str = "pending"        # pending | running | done | error | cancelled
    started_at: float = 0.0
    ended_at: float = 0.0
    progress: float = 0.0
    events: List[Event] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    cancel_requested: bool = False

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _wake: threading.Event = field(default_factory=threading.Event, repr=False)

    def push(self, level: str, message: str, *,
             progress: Optional[float] = None,
             data: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self.events.append(Event(time.time(), level, message, progress, data))
            if progress is not None:
                self.progress = max(self.progress, float(progress))
        self._wake.set()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "state": self.state,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "progress": self.progress,
                "result": self.result,
                "error": self.error,
                "cancel_requested": self.cancel_requested,
                "events_count": len(self.events),
            }

    def events_after(self, idx: int) -> List[Dict[str, Any]]:
        with self._lock:
            return [e.as_dict() for e in self.events[idx:]]


_JOBS: Dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def _new_job(kind: str) -> Job:
    j = Job(id=uuid.uuid4().hex[:12], kind=kind)
    with _JOBS_LOCK:
        _JOBS[j.id] = j
    return j


def get_job(jid: str) -> Optional[Job]:
    with _JOBS_LOCK:
        return _JOBS.get(jid)


def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    with _JOBS_LOCK:
        items = list(_JOBS.values())
    items.sort(key=lambda j: j.started_at or 0, reverse=True)
    return [j.snapshot() for j in items[:limit]]


def cancel_job(jid: str) -> bool:
    j = get_job(jid)
    if not j:
        return False
    j.cancel_requested = True
    j._wake.set()
    j.push("warn", "cancel requested")
    return True


def _run_job(j: Job, fn: Callable[[Job], Dict[str, Any]]) -> None:
    j.state = "running"
    j.started_at = time.time()
    j.push("info", f"starting {j.kind}")
    try:
        result = fn(j) or {}
        if j.cancel_requested:
            j.state = "cancelled"
            j.push("warn", "cancelled")
        else:
            j.state = "done"
            j.progress = 100.0
            j.result = result
            j.push("info", "done", progress=100.0, data=result)
    except Exception as e:
        j.state = "error"
        j.error = f"{type(e).__name__}: {e}"
        j.push("error", j.error)
    finally:
        j.ended_at = time.time()
        j._wake.set()


def start_job(kind: str, fn: Callable[[Job], Dict[str, Any]]) -> Job:
    """Spawn a background thread running fn(job)."""
    j = _new_job(kind)
    t = threading.Thread(target=_run_job, args=(j, fn), daemon=True, name=f"setup-{kind}-{j.id}")
    t.start()
    return j


# ============================================================
# Helpers
# ============================================================

def _slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")
    return s or "model"


def _file_size(path: Path) -> int:
    try: return path.stat().st_size
    except Exception: return 0


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try: total += p.stat().st_size
            except Exception: pass
    return total


def _human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(f) < 1024.0: return f"{f:.1f}{unit}"
        f /= 1024.0
    return f"{f:.1f}PB"


# ============================================================
# Downloads
# ============================================================

def download_hf_snapshot(repo_id: str,
                         dest: Optional[str] = None,
                         job: Optional[Job] = None,
                         revision: Optional[str] = None) -> Dict[str, Any]:
    """
    Snapshot-download a HuggingFace repo.
    Default dest: models/hf_local/<slug>/.
    Skips TF/Flax weight files.
    Returns {repo_id, path, size_bytes, files: [...]}.
    """
    try:
        from huggingface_hub import snapshot_download
    except Exception as e:
        raise RuntimeError(
            "huggingface_hub is not installed. Run: pip install huggingface_hub"
        ) from e

    if job: job.push("info", f"resolving {repo_id}…")

    target = Path(dest) if dest else (HF_LOCAL_DIR / _slugify(repo_id))
    target.mkdir(parents=True, exist_ok=True)

    if job and job.cancel_requested:
        return {"cancelled": True}

    if job: job.push("info", f"downloading to {target}…", progress=5.0)

    path = snapshot_download(
        repo_id=repo_id,
        revision=revision,
        local_dir=str(target),
        local_dir_use_symlinks=False,
        ignore_patterns=["*.msgpack", "*.h5", "*.ot", "*.tflite"],
    )

    files = []
    for p in sorted(Path(path).rglob("*")):
        if p.is_file():
            files.append({
                "path": str(p.relative_to(path)),
                "size": _file_size(p),
            })

    size = _dir_size(Path(path))
    if job:
        job.push("info", f"snapshot complete — {len(files)} files, {_human_bytes(size)}",
                 progress=95.0, data={"path": path, "size": size})

    return {
        "repo_id": repo_id,
        "path": str(path),
        "size_bytes": size,
        "size_human": _human_bytes(size),
        "files": files[:200],   # cap to avoid huge payloads
        "files_total": len(files),
    }


def download_gguf(source: str,
                  filename: Optional[str] = None,
                  dest: Optional[str] = None,
                  job: Optional[Job] = None) -> Dict[str, Any]:
    """
    Download a single GGUF model file.

    `source` accepts either:
      - a full HTTP(S) URL — saved to dest/ with the filename from the URL
      - an HF repo "owner/repo" — `filename` must be provided (e.g.
        "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF" + "tinyllama-1.1b-chat-v1.0.Q2_K.gguf")
      - a combined "owner/repo:filename"

    Default dest: models/.
    Returns {path, size_bytes, source}.
    """
    target_dir = Path(dest) if dest else MODELS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    # Combined "owner/repo:filename" form
    if ":" in source and not source.startswith(("http://", "https://")):
        repo_id, filename = source.split(":", 1)
        source = repo_id

    if source.startswith(("http://", "https://")):
        return _download_url(source, target_dir, job)

    # HF Hub single-file
    try:
        from huggingface_hub import hf_hub_download
    except Exception as e:
        raise RuntimeError(
            "huggingface_hub is not installed. Run: pip install huggingface_hub"
        ) from e

    if not filename:
        raise ValueError("filename is required when downloading from a HF repo. "
                         "Use 'owner/repo:filename.gguf' or supply filename separately.")

    if job: job.push("info", f"downloading {source}/{filename} via HF Hub…", progress=5.0)
    if job and job.cancel_requested:
        return {"cancelled": True}

    path = hf_hub_download(
        repo_id=source,
        filename=filename,
        local_dir=str(target_dir),
        local_dir_use_symlinks=False,
    )
    size = _file_size(Path(path))
    if job:
        job.push("info", f"download complete — {_human_bytes(size)}",
                 progress=95.0, data={"path": path, "size": size})
    return {
        "source": f"{source}:{filename}",
        "path": str(path),
        "size_bytes": size,
        "size_human": _human_bytes(size),
    }


def _download_url(url: str, target_dir: Path, job: Optional[Job]) -> Dict[str, Any]:
    name = url.rsplit("/", 1)[-1].split("?", 1)[0] or "download.bin"
    out = target_dir / name
    if job: job.push("info", f"downloading {url} → {out}", progress=5.0)

    last_pct = [0]
    def _hook(blocks: int, block_size: int, total: int):
        if job and job.cancel_requested:
            raise RuntimeError("cancelled")
        if total <= 0: return
        pct = min(95.0, (blocks * block_size) * 100.0 / total)
        if pct - last_pct[0] >= 1.0:
            last_pct[0] = pct
            if job:
                job.push("info", f"{int(pct)}% downloaded", progress=pct)

    try:
        urllib.request.urlretrieve(url, out, reporthook=_hook)
    except RuntimeError as e:
        if "cancelled" in str(e):
            try: out.unlink()
            except Exception: pass
            return {"cancelled": True}
        raise

    size = _file_size(out)
    if job:
        job.push("info", f"saved {_human_bytes(size)}", progress=95.0,
                 data={"path": str(out), "size": size})
    return {"source": url, "path": str(out), "size_bytes": size, "size_human": _human_bytes(size)}


# ============================================================
# Auto-setup wrappers
# ============================================================

def auto_recursive(force: bool = False,
                   install_deps: bool = False,
                   download: bool = False,
                   job: Optional[Job] = None) -> Dict[str, Any]:
    """
    Wraps agcl.autoconfig.run() and captures stdout into the job log.
    """
    from agcl import autoconfig as A

    if job: job.push("info",
                     f"running autoconfig (force={force}, install_deps={install_deps}, download={download})",
                     progress=5.0)

    buf = io.StringIO()
    code = 0
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            code = A.run(force=force, install_deps=install_deps, download=download)
    except SystemExit as e:
        code = int(getattr(e, "code", 0) or 0)
    except Exception as e:
        if job: job.push("error", f"autoconfig threw: {type(e).__name__}: {e}")
        raise

    log = buf.getvalue()
    for line in log.splitlines():
        if line.strip() and job:
            job.push("info", line.rstrip(), progress=None)

    if job: job.push("info", f"autoconfig exit code {code}", progress=95.0)

    return {
        "exit_code": code,
        "force": force,
        "install_deps": install_deps,
        "download": download,
        "log": log,
        "mas_json": str(PROJECT_ROOT / "mas.json"),
        "env_file": str(PROJECT_ROOT / ".env"),
    }


def auto_chat(local_model_path: Optional[str] = None,
              default_cloud: Optional[str] = None,
              prefix_word_count: Optional[int] = None,
              prompt_format: Optional[str] = None,
              job: Optional[Job] = None) -> Dict[str, Any]:
    """
    Patches .env with the chat-relevant defaults: LOCAL_MODEL_PATH,
    DEFAULT_CLOUD, PREFIX_WORD_COUNT, PROMPT_FORMAT. Skips any field
    left as None. Reports which keys changed and any sanity warnings.
    """
    updates: Dict[str, str] = {}
    warnings: List[str] = []

    if local_model_path:
        p = Path(local_model_path)
        if not p.exists():
            warnings.append(f"LOCAL_MODEL_PATH does not exist on disk: {p}")
        elif not p.suffix.lower() == ".gguf":
            warnings.append(f"LOCAL_MODEL_PATH is not a .gguf file: {p}")
        updates["LOCAL_MODEL_PATH"] = str(p)

    if default_cloud:
        if default_cloud not in ("claude", "openai"):
            raise ValueError("default_cloud must be 'claude' or 'openai'")
        updates["DEFAULT_CLOUD"] = default_cloud
        # warn about missing key
        if default_cloud == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
            warnings.append("ANTHROPIC_API_KEY is not set in the environment.")
        if default_cloud == "openai" and not os.environ.get("OPENAI_API_KEY"):
            warnings.append("OPENAI_API_KEY is not set in the environment.")

    if prefix_word_count is not None:
        if not (1 <= int(prefix_word_count) <= 32):
            raise ValueError("prefix_word_count must be between 1 and 32")
        updates["PREFIX_WORD_COUNT"] = str(int(prefix_word_count))

    if prompt_format:
        if prompt_format not in ("chatml", "plain"):
            raise ValueError("prompt_format must be 'chatml' or 'plain'")
        updates["PROMPT_FORMAT"] = prompt_format

    if not updates:
        return {"applied": [], "warnings": warnings, "note": "no fields supplied — nothing to do"}

    if job: job.push("info", f"patching .env with {len(updates)} keys", progress=20.0)

    _patch_env_file(updates)

    if job:
        for k, v in updates.items():
            job.push("info", f".env: {k}={v}")
        for w in warnings:
            job.push("warn", w)

    return {
        "applied": list(updates.keys()),
        "values": updates,
        "warnings": warnings,
        "env_file": str(PROJECT_ROOT / ".env"),
    }


def _patch_env_file(updates: Dict[str, str]) -> None:
    """Idempotent .env patcher.

    - For each updated key, the LAST matching line wins (older
      duplicates are dropped — fixes a class of bugs where the file
      accumulated duplicate KEY=… lines across runs).
    - For each updated key not present, append at the end.
    - Lines that are neither matched-and-updated nor matched-and-dropped
      are preserved (comments, blank lines, unrelated keys).
    """
    env_path = PROJECT_ROOT / ".env"
    lines: List[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    # First pass: dedupe ALL keys (not just updated ones) — keep the
    # last occurrence of every KEY=… line. Preserves order otherwise.
    last_idx_for_key: Dict[str, int] = {}
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if m:
            last_idx_for_key[m.group(1)] = i

    out: List[str] = []
    seen: set = set()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if not m:
            out.append(line)
            continue
        key = m.group(1)
        if i != last_idx_for_key[key]:
            # Older duplicate — drop.
            continue
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)

    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ============================================================
# Local model inventory + registry
# ============================================================

def list_local_models() -> Dict[str, Any]:
    """Scan conventional model directories. Returns GGUF files + HF model dirs."""
    gguf: List[Dict[str, Any]] = []
    hf_dirs: List[Dict[str, Any]] = []

    # GGUF: scan models/ (top-level) and models/gguf/
    for root in [MODELS_DIR, MODELS_DIR / "gguf"]:
        if root.exists():
            for p in root.rglob("*.gguf"):
                gguf.append({
                    "path": str(p),
                    "rel": str(p.relative_to(PROJECT_ROOT)) if PROJECT_ROOT in p.parents else str(p),
                    "size_bytes": _file_size(p),
                    "size_human": _human_bytes(_file_size(p)),
                })

    # HF snapshots:
    # - models/hf_local/ — every immediate subdir is a snapshot we created
    # - models/hf/hub/, ~/.cache/huggingface/hub/ — only "models--..." dirs
    seen_paths: set = set()

    def _walk_hf_cache(root: Path):
        """HF caches put real snapshots in `models--owner--name/` dirs.
        Skip `.locks`, `xet`, and any dataset cache dirs."""
        if not root.exists(): return
        for p in root.iterdir():
            if not p.is_dir(): continue
            if not p.name.startswith("models--"): continue
            if str(p) in seen_paths: continue
            seen_paths.add(str(p))
            name = p.name.replace("models--", "", 1).replace("--", "/")
            hf_dirs.append({
                "name": name,
                "path": str(p),
                "size_bytes": _dir_size(p),
                "size_human": _human_bytes(_dir_size(p)),
                "source": str(root),
            })

    # Project-local hf_local: each top-level subdir is a snapshot
    if HF_LOCAL_DIR.exists():
        for p in HF_LOCAL_DIR.iterdir():
            if not p.is_dir(): continue
            if str(p) in seen_paths: continue
            seen_paths.add(str(p))
            hf_dirs.append({
                "name": p.name,
                "path": str(p),
                "size_bytes": _dir_size(p),
                "size_human": _human_bytes(_dir_size(p)),
                "source": str(HF_LOCAL_DIR),
            })

    # HF "hub" cache layouts (project-local + user)
    _walk_hf_cache(HF_CACHE_DIR / "hub")
    _walk_hf_cache(Path(os.path.expanduser("~/.cache/huggingface/hub")))

    return {
        "gguf": sorted(gguf, key=lambda x: x["path"]),
        "hf": sorted(hf_dirs, key=lambda x: x["name"]),
        "scanned": [str(MODELS_DIR), str(HF_LOCAL_DIR), str(HF_CACHE_DIR),
                    "~/.cache/huggingface/hub"],
    }


def model_registry() -> Dict[str, Any]:
    """
    Curated list of recommended models. The GUI quick-pick uses these.
    Sizes are approximate and informational only.
    """
    return {
        "local_chat_gguf": [
            {
                "label": "SmolLM2-135M (Q2_K) — tiny + fast prefix model",
                "source": "bartowski/SmolLM2-135M-Instruct-GGUF",
                "filename": "SmolLM2-135M-Instruct-Q2_K.gguf",
                "size_human": "~75 MB",
                "use": "local prefix",
            },
            {
                "label": "TinyLlama-1.1B Chat (Q2_K) — small chat head",
                "source": "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
                "filename": "tinyllama-1.1b-chat-v1.0.Q2_K.gguf",
                "size_human": "~480 MB",
                "use": "local prefix or chat",
            },
            {
                "label": "Qwen2.5-0.5B Instruct (Q4_K_M)",
                "source": "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
                "filename": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
                "size_human": "~390 MB",
                "use": "local prefix or chat",
            },
            {
                "label": "Llama-3.2-1B Instruct (Q4_K_M)",
                "source": "bartowski/Llama-3.2-1B-Instruct-GGUF",
                "filename": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
                "size_human": "~770 MB",
                "use": "local prefix or chat",
            },
        ],
        "mas_hf": [
            {
                "label": "Qwen2.5-0.5B Instruct (HF) — planner agent",
                "repo_id": "Qwen/Qwen2.5-0.5B-Instruct",
                "size_human": "~1 GB",
                "use": "MAS planner",
            },
            {
                "label": "TinyLlama-1.1B Chat (HF) — solver agent",
                "repo_id": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
                "size_human": "~2.2 GB",
                "use": "MAS solver",
            },
            {
                "label": "Phi-3.5-mini-instruct — alternative MAS agent",
                "repo_id": "microsoft/Phi-3.5-mini-instruct",
                "size_human": "~7 GB",
                "use": "MAS heavy-weight agent",
            },
        ],
        "canonical_mas_pair": {
            "planner": "Qwen/Qwen2.5-0.5B-Instruct",
            "solver":  "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            "total_size_human": "~3 GB",
        },
    }
