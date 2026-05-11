"""
agcl.wizards — declarative deploy wizards.

One wizard per toolkit / external service. Each wizard is a list of
ordered "steps". Steps are interpreted by a shared executor so the
exact same descriptor drives the CLI (`agcl deploy <name>`) and the
web UI (Setup tab → Deploy wizards).

Step kinds
----------
info        Show text, single "Continue" button.
open_url    Show a button to open a URL in the user's browser
            (creating tokens, joining a bot, etc.).
input       Free-text field. `secret: True` for masked input.
            Optional `validate` action runs server-side before
            advancing.
choice      Pick one of `options: [{value, label}, ...]`.
confirm     Yes/no.
action      Server-side: run a named action (validate token via API,
            docker-run a container, ping a URL, generate an invite
            link, etc.). Result is stored under `action_result` for
            later steps to reference.
save_env    Server-side: write collected answers to .env. Mapping
            given as `keys: {ENV_VAR: answer_key}`.
wait_for    Server-side: poll a URL (or named check) until ready
            or timeout.
summary     Terminal step. `body` is template-rendered using the
            collected answers + action_results.

Templating
----------
Strings in `body`, `url`, etc. can reference `{answers.key}` or
`{result.key}` — they're rendered at step-display time. This lets a
later step show "Bot logged in as {result.bot_username}" using the
output of an earlier action.

Session protocol
----------------
1. `start(name)` -> creates a session, runs server-side steps until
   one blocks for the user, returns that step.
2. `answer(session_id, value)` -> stores the answer, runs server-side
   steps until next user-blocking step, returns it. When the wizard
   ends, returns `{state: "done", summary: ...}`.
3. `cancel(session_id)` -> marks abandoned.

Sessions are in-memory only — same model as setup.py jobs. A node
restart drops them.
"""

from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ============================================================
# Step descriptors are plain dicts — see WIZARDS at the bottom.
# A step has at least {"id": str, "type": str, "title": str}.
# ============================================================


# ============================================================
# Session manager
# ============================================================

@dataclass
class Session:
    id: str
    wizard: str
    answers: Dict[str, Any] = field(default_factory=dict)
    results: Dict[str, Any] = field(default_factory=dict)   # output of action steps
    cursor: int = 0
    state: str = "running"                                  # running | done | error | cancelled
    last_step: Dict[str, Any] = field(default_factory=dict) # last client-blocking step
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    log: List[str] = field(default_factory=list)


_SESSIONS: Dict[str, Session] = {}
_LOCK = threading.Lock()


def _new_session(wizard_name: str) -> Session:
    s = Session(id=uuid.uuid4().hex[:12], wizard=wizard_name)
    with _LOCK:
        _SESSIONS[s.id] = s
    return s


def get_session(sid: str) -> Optional[Session]:
    with _LOCK:
        return _SESSIONS.get(sid)


def list_sessions(limit: int = 30) -> List[Dict[str, Any]]:
    with _LOCK:
        sess = sorted(_SESSIONS.values(), key=lambda s: s.started_at, reverse=True)
    return [_session_snapshot(s) for s in sess[:limit]]


def _session_snapshot(s: Session) -> Dict[str, Any]:
    return {
        "id": s.id, "wizard": s.wizard, "state": s.state,
        "cursor": s.cursor, "error": s.error, "started_at": s.started_at,
        "log": s.log[-30:],
    }


def cancel_session(sid: str) -> bool:
    s = get_session(sid)
    if not s: return False
    s.state = "cancelled"
    return True


# ============================================================
# Template rendering
# ============================================================

def _render(template: Any, sess: Session) -> Any:
    """Recursively render {answers.X} and {result.X} placeholders."""
    if isinstance(template, str):
        out = template
        # answers
        for m in re.finditer(r"\{answers\.([a-zA-Z0-9_]+)\}", out):
            key = m.group(1)
            out = out.replace(m.group(0), str(sess.answers.get(key, "")))
        # results
        for m in re.finditer(r"\{result\.([a-zA-Z0-9_.]+)\}", out):
            path = m.group(1).split(".")
            v: Any = sess.results
            try:
                for p in path: v = v[p]
            except (KeyError, TypeError):
                v = ""
            out = out.replace(m.group(0), str(v))
        return out
    if isinstance(template, dict):
        return {k: _render(v, sess) for k, v in template.items()}
    if isinstance(template, list):
        return [_render(v, sess) for v in template]
    return template


# ============================================================
# Action registry — server-side steps call into here
# ============================================================

_ACTIONS: Dict[str, Callable[[Session, Dict[str, Any]], Dict[str, Any]]] = {}


def register_action(name: str):
    def deco(fn):
        _ACTIONS[name] = fn
        return fn
    return deco


def list_actions() -> List[str]:
    return sorted(_ACTIONS.keys())


# ============================================================
# Executor
# ============================================================

CLIENT_BLOCKING = {"info", "open_url", "input", "choice", "confirm", "summary"}
SERVER_RUN     = {"action", "save_env", "wait_for"}


def _advance(sess: Session) -> Dict[str, Any]:
    """Run server-side steps until we hit one that needs the client,
    or the wizard ends."""
    desc = get_wizard(sess.wizard)
    if not desc:
        sess.state = "error"; sess.error = f"unknown wizard: {sess.wizard}"
        return _session_snapshot(sess)
    steps = desc["steps"]

    while sess.cursor < len(steps):
        if sess.state in ("cancelled", "error"):
            return _session_snapshot(sess)
        step = steps[sess.cursor]
        kind = step.get("type")
        if kind in SERVER_RUN:
            sess.log.append(f"server-run {step.get('id', '?')} ({kind})")
            try:
                if kind == "action":
                    name = step["action"]
                    fn = _ACTIONS.get(name)
                    if not fn:
                        raise RuntimeError(f"unknown action '{name}'")
                    res = fn(sess, _render(step, sess)) or {}
                    sess.results[step["id"]] = res
                elif kind == "save_env":
                    _save_env(sess, _render(step, sess))
                elif kind == "wait_for":
                    res = _wait_for(sess, _render(step, sess))
                    sess.results[step["id"]] = res
            except Exception as e:
                sess.state = "error"
                sess.error = f"{type(e).__name__}: {e}"
                sess.log.append(f"  error: {sess.error}")
                return _session_snapshot(sess)
            sess.cursor += 1
            continue
        if kind in CLIENT_BLOCKING:
            sess.last_step = _render(step, sess)
            if kind == "summary":
                # show the summary, then mark done
                sess.state = "done"
            return {**_session_snapshot(sess), "step": sess.last_step}
        # Unknown step type — skip
        sess.log.append(f"  warn: unknown step type '{kind}'")
        sess.cursor += 1

    sess.state = "done"
    return _session_snapshot(sess)


def start(name: str) -> Dict[str, Any]:
    desc = get_wizard(name)
    if not desc:
        raise ValueError(f"unknown wizard: {name}")
    s = _new_session(name)
    return _advance(s)


def answer(sid: str, value: Any) -> Dict[str, Any]:
    sess = get_session(sid)
    if not sess: raise ValueError("session not found")
    if sess.state != "running": return _session_snapshot(sess)
    step = sess.last_step
    if step.get("type") in ("info", "open_url"):
        # No real answer — treat as "Continue"
        pass
    elif step.get("type") in ("input", "choice", "confirm"):
        key = step.get("key") or step.get("id")
        sess.answers[key] = value
    sess.cursor += 1
    return _advance(sess)


# ============================================================
# Built-in server-side step implementations
# ============================================================

def _save_env(sess: Session, step: Dict[str, Any]) -> None:
    from agcl.setup import _patch_env_file
    mapping = step.get("keys") or {}
    updates: Dict[str, str] = {}
    for env_name, source in mapping.items():
        # source can be "answers.X", "result.X.y", or a literal value
        if isinstance(source, str) and source.startswith("answers."):
            updates[env_name] = str(sess.answers.get(source.split(".", 1)[1], ""))
        elif isinstance(source, str) and source.startswith("result."):
            path = source.split(".")[1:]
            v: Any = sess.results
            for p in path:
                v = v.get(p, "") if isinstance(v, dict) else ""
            updates[env_name] = str(v) if v is not None else ""
        else:
            updates[env_name] = str(source)
    _patch_env_file({k: v for k, v in updates.items() if v != ""})
    # also reflect to live os.environ so other actions see it
    for k, v in updates.items():
        if v: os.environ[k] = v


def _wait_for(sess: Session, step: Dict[str, Any]) -> Dict[str, Any]:
    import urllib.request
    url = step["url"]
    timeout = float(step.get("timeout", 60.0))
    interval = float(step.get("interval", 2.0))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status < 500:
                    return {"ok": True, "status": r.status, "url": url}
        except Exception:
            pass
        time.sleep(interval)
    return {"ok": False, "timeout": timeout, "url": url}


# ============================================================
# Concrete actions
# ============================================================

@register_action("validate_discord_token")
def _validate_discord(sess, step):
    from agcl.toolkit.discord import get_me
    import asyncio
    token = sess.answers.get(step.get("token_key", "bot_token"))
    if not token:
        raise RuntimeError("bot_token answer missing")
    me = asyncio.run(get_me(bot_token=token))
    return {"ok": True, "id": me.get("id"), "username": me.get("username")}


@register_action("discord_invite_url")
def _discord_invite(sess, step):
    app_id = sess.answers.get("app_id") or sess.results.get("validate", {}).get("id")
    if not app_id:
        raise RuntimeError("application id missing — paste it in the previous step or validate the bot token first")
    # Permissions: send messages, read message history, use slash commands, embed links
    perms = 2147747840
    scope = "bot+applications.commands"
    url = f"https://discord.com/oauth2/authorize?client_id={app_id}&scope={scope}&permissions={perms}"
    return {"invite_url": url}


@register_action("validate_aws_keys")
def _validate_aws(sess, step):
    """Hit STS GetCallerIdentity using only stdlib + sigv4-lite."""
    import hashlib, hmac, json
    from urllib.request import Request, urlopen
    ak = sess.answers.get("access_key_id", "")
    sk = sess.answers.get("secret_access_key", "")
    region = sess.answers.get("region", "us-east-1") or "us-east-1"
    if not ak or not sk:
        raise RuntimeError("AWS access key + secret required")
    # STS GetCallerIdentity via sigv4 — minimal implementation
    host = "sts.amazonaws.com"
    endpoint = f"https://{host}/"
    body = "Action=GetCallerIdentity&Version=2011-06-15"
    t = time.gmtime()
    amzdate    = time.strftime("%Y%m%dT%H%M%SZ", t)
    datestamp  = time.strftime("%Y%m%d", t)
    canonical_headers = f"content-type:application/x-www-form-urlencoded\nhost:{host}\nx-amz-date:{amzdate}\n"
    signed_headers = "content-type;host;x-amz-date"
    payload_hash = hashlib.sha256(body.encode()).hexdigest()
    canonical_request = f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    algo = "AWS4-HMAC-SHA256"
    cred_scope = f"{datestamp}/{region}/sts/aws4_request"
    string_to_sign = f"{algo}\n{amzdate}\n{cred_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    def sign(k, msg): return hmac.new(k, msg.encode(), hashlib.sha256).digest()
    kDate    = sign(("AWS4" + sk).encode(), datestamp)
    kRegion  = sign(kDate, region)
    kService = sign(kRegion, "sts")
    kSigning = sign(kService, "aws4_request")
    sig = hmac.new(kSigning, string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth = f"{algo} Credential={ak}/{cred_scope}, SignedHeaders={signed_headers}, Signature={sig}"
    req = Request(endpoint, data=body.encode(),
                  headers={"Content-Type": "application/x-www-form-urlencoded",
                           "X-Amz-Date": amzdate, "Authorization": auth, "Accept": "application/json"},
                  method="POST")
    try:
        resp = urlopen(req, timeout=10).read().decode()
    except Exception as e:
        raise RuntimeError(f"STS rejected keys: {e}")
    arn   = re.search(r"<Arn>([^<]+)</Arn>", resp)
    acct  = re.search(r"<Account>([^<]+)</Account>", resp)
    uid   = re.search(r"<UserId>([^<]+)</UserId>", resp)
    return {"ok": True, "arn": arn.group(1) if arn else "",
            "account": acct.group(1) if acct else "",
            "user_id": uid.group(1) if uid else ""}


@register_action("validate_huggingface_token")
def _validate_hf(sess, step):
    import json
    from urllib.request import Request, urlopen
    token = sess.answers.get("hf_token")
    if not token: raise RuntimeError("token missing")
    req = Request("https://huggingface.co/api/whoami-v2",
                  headers={"Authorization": f"Bearer {token}"})
    raw = urlopen(req, timeout=10).read().decode()
    j = json.loads(raw)
    return {"ok": True, "name": j.get("name"), "email": j.get("email"),
            "orgs": [o.get("name") for o in (j.get("orgs") or [])]}


@register_action("validate_cloudflare_token")
def _validate_cf(sess, step):
    import json
    from urllib.request import Request, urlopen
    token = sess.answers.get("cf_token")
    if not token: raise RuntimeError("token missing")
    req = Request("https://api.cloudflare.com/client/v4/user/tokens/verify",
                  headers={"Authorization": f"Bearer {token}"})
    try:
        raw = urlopen(req, timeout=10).read().decode()
        j = json.loads(raw)
        if j.get("success"):
            return {"ok": True, "id": j.get("result", {}).get("id"),
                    "status": j.get("result", {}).get("status")}
    except Exception as e:
        raise RuntimeError(f"Cloudflare rejected token: {e}")
    raise RuntimeError("Cloudflare token invalid")


@register_action("validate_github_pat")
def _validate_gh(sess, step):
    import json
    from urllib.request import Request, urlopen
    token = sess.answers.get("gh_token")
    if not token: raise RuntimeError("token missing")
    req = Request("https://api.github.com/user",
                  headers={"Authorization": f"Bearer {token}",
                           "Accept": "application/vnd.github+json"})
    raw = urlopen(req, timeout=10).read().decode()
    j = json.loads(raw)
    return {"ok": True, "login": j.get("login"), "name": j.get("name"), "id": j.get("id")}


@register_action("docker_login_ghcr")
def _docker_login_ghcr(sess, step):
    from agcl.integrations_auth import docker_login
    user = sess.results.get("validate", {}).get("login") or sess.answers.get("gh_login")
    token = sess.answers.get("gh_token")
    if not user or not token: raise RuntimeError("login or token missing")
    r = docker_login("ghcr.io", user, token, persist_reference=True)
    if not r.get("ok"): raise RuntimeError(r.get("message", "docker login failed"))
    return r


@register_action("validate_openai_key")
def _validate_openai(sess, step):
    from agcl.integrations_auth import check_openai_key
    r = check_openai_key(sess.answers.get("api_key"))
    if not r.get("ok"): raise RuntimeError(r.get("message", "invalid"))
    return r


@register_action("validate_anthropic_key")
def _validate_anthropic(sess, step):
    from agcl.integrations_auth import check_anthropic_key
    r = check_anthropic_key(sess.answers.get("api_key"))
    if not r.get("ok"): raise RuntimeError(r.get("message", "invalid"))
    return r


@register_action("validate_deepseek_key")
def _validate_deepseek(sess, step):
    from agcl.integrations_auth import check_deepseek_key
    r = check_deepseek_key(sess.answers.get("api_key"))
    if not r.get("ok"): raise RuntimeError(r.get("message", "invalid"))
    return r


@register_action("pip_install")
def _pip_install(sess, step):
    """Install one or more Python packages into the running interpreter.

    Idempotent: skips packages whose `check_import` already imports.
    Auto-detects `uv pip` vs plain `pip` so both install paths work.
    """
    import importlib, shutil, subprocess, sys
    packages = step.get("packages") or []
    if isinstance(packages, str): packages = [packages]
    if not packages:
        return {"ok": True, "installed": [], "skipped": [], "note": "no packages"}

    installed: list = []
    skipped:   list = []
    failed:    list = []

    for p in packages:
        # `p` can be {"pip": "discord.py", "import": "discord"} or just a string
        pip_name    = p["pip"]    if isinstance(p, dict) else p
        import_name = p.get("import", pip_name) if isinstance(p, dict) else pip_name
        # de-version: "redis[hiredis]" -> "redis"
        import_name = import_name.split("[", 1)[0].split("==", 1)[0]
        try:
            importlib.import_module(import_name)
            skipped.append(pip_name)
            continue
        except ImportError:
            pass
        # Pick installer
        if shutil.which("uv"):
            cmd = ["uv", "pip", "install", "--python", sys.executable, pip_name]
        else:
            cmd = [sys.executable, "-m", "pip", "install", pip_name]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                installed.append(pip_name)
                # invalidate cache + try import to confirm
                importlib.invalidate_caches()
                try: importlib.import_module(import_name)
                except ImportError:
                    failed.append({"pip": pip_name, "reason": "installed but still not importable"})
            else:
                failed.append({"pip": pip_name, "stderr": (r.stderr or r.stdout)[-400:]})
        except Exception as e:
            failed.append({"pip": pip_name, "error": str(e)})

    if failed:
        msg = "; ".join([f"{f['pip']}: {f.get('stderr') or f.get('error') or f.get('reason')}" for f in failed])
        raise RuntimeError(f"pip install failed — {msg}")
    return {"ok": True, "installed": installed, "skipped": skipped}


@register_action("docker_run_detached")
def _docker_run(sess, step):
    """Run a `docker run` command in the background and return the container id."""
    import shutil, subprocess
    if not shutil.which("docker"):
        raise RuntimeError("`docker` not on PATH — install Docker first")
    cmd = step["cmd"]
    name = step.get("name", "")
    if not isinstance(cmd, list): raise RuntimeError("cmd must be a list")
    # If named, kill any prior container first
    if name:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"docker run failed: {r.stderr.strip() or r.stdout.strip()}")
    return {"ok": True, "container_id": r.stdout.strip(),
            "name": name, "command": " ".join(cmd)}


@register_action("ollama_pull")
def _ollama_pull(sess, step):
    """Pull a model via `ollama pull`. Assumes ollama is installed."""
    import shutil, subprocess
    if not shutil.which("ollama"):
        raise RuntimeError("`ollama` not on PATH")
    model = sess.answers.get("model") or step.get("default_model", "llama3.2:1b")
    r = subprocess.run(["ollama", "pull", model], capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"pull failed: {r.stderr.strip()}")
    return {"ok": True, "model": model}


@register_action("ping_url")
def _ping_url(sess, step):
    """Probe a URL with a GET. Doesn't fail on non-2xx — just returns status."""
    import urllib.request
    url = step.get("url") or _render(step.get("url_template", ""), sess)
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return {"ok": r.status < 500, "status": r.status, "url": url}
    except Exception as e:
        return {"ok": False, "error": str(e), "url": url}


@register_action("register_custom_provider")
def _register_provider(sess, step):
    """Register the answers as a custom OpenAI-compatible provider in /node/usage."""
    from agcl.usage import register_custom_provider
    name = sess.answers.get("name") or step.get("default_name")
    base = sess.answers.get("base_url")
    keyenv = sess.answers.get("api_key_env")
    model = sess.answers.get("model")
    kind = sess.answers.get("kind") or "chat"
    if not (name and base and keyenv and model):
        raise RuntimeError("name, base_url, api_key_env and model are all required")
    register_custom_provider(name=name, base_url=base, api_key_env=keyenv, model=model, kind=kind)
    return {"ok": True, "name": name}


# ============================================================
# Wizard descriptors
# ============================================================
#
# Each wizard is a dict: {title, summary, category, steps: [...]}.
# Categories let the GUI group them: "bot", "cloud", "model", "store",
# "infra", "provider".

WIZARDS: Dict[str, Dict[str, Any]] = {

    # -----------------------------------------------------------------
    # PROVIDER KEYS — simple "paste token, validate, save" wizards
    # -----------------------------------------------------------------

    "openai": {
        "title": "Connect OpenAI",
        "summary": "Paste an OpenAI API key, validate it, save to .env.",
        "category": "provider",
        "steps": [
            {"id": "intro", "type": "info", "title": "Connect OpenAI",
             "body": "We'll validate the key against api.openai.com/v1/models and save it to .env. The key never leaves this machine."},
            {"id": "open_console", "type": "open_url", "title": "Get your key",
             "url": "https://platform.openai.com/api-keys",
             "body": "Click 'Create new secret key' on the OpenAI page that just opened."},
            {"id": "ask_key", "type": "input", "key": "api_key", "title": "Paste the key", "secret": True,
             "placeholder": "sk-..."},
            {"id": "validate", "type": "action", "action": "validate_openai_key",
             "title": "Validating key with OpenAI..."},
            {"id": "save", "type": "save_env", "keys": {"OPENAI_API_KEY": "answers.api_key"}},
            {"id": "done", "type": "summary", "title": "OpenAI connected",
             "body": "Key works — {result.validate.message}.\nSaved to .env as OPENAI_API_KEY.\nUse the Chat tab to send a message."},
        ],
    },

    "anthropic": {
        "title": "Connect Anthropic (Claude)",
        "summary": "Paste an Anthropic API key, validate it, save to .env.",
        "category": "provider",
        "steps": [
            {"id": "intro", "type": "info", "title": "Connect Anthropic",
             "body": "We'll validate by listing models, then save to .env."},
            {"id": "open_console", "type": "open_url", "title": "Get your key",
             "url": "https://console.anthropic.com/settings/keys",
             "body": "Click 'Create Key' on the Anthropic console."},
            {"id": "ask_key", "type": "input", "key": "api_key", "title": "Paste the key", "secret": True,
             "placeholder": "sk-ant-..."},
            {"id": "validate", "type": "action", "action": "validate_anthropic_key"},
            {"id": "save", "type": "save_env", "keys": {"ANTHROPIC_API_KEY": "answers.api_key"}},
            {"id": "done", "type": "summary", "title": "Anthropic connected",
             "body": "{result.validate.message}\nSaved as ANTHROPIC_API_KEY."},
        ],
    },

    "deepseek": {
        "title": "Connect DeepSeek",
        "summary": "Paste a DeepSeek API key, validate, save.",
        "category": "provider",
        "steps": [
            {"id": "intro", "type": "info", "title": "Connect DeepSeek", "body": "DeepSeek is OpenAI-API compatible. Setup → Cloud providers will surface your remaining balance once this is done."},
            {"id": "open_console", "type": "open_url", "title": "Get your key", "url": "https://platform.deepseek.com/api_keys"},
            {"id": "ask_key", "type": "input", "key": "api_key", "title": "Paste the key", "secret": True, "placeholder": "sk-..."},
            {"id": "validate", "type": "action", "action": "validate_deepseek_key"},
            {"id": "save", "type": "save_env", "keys": {"DEEPSEEK_API_KEY": "answers.api_key"}},
            {"id": "done", "type": "summary", "title": "DeepSeek connected",
             "body": "{result.validate.message}\nSaved as DEEPSEEK_API_KEY."},
        ],
    },

    "huggingface": {
        "title": "Connect HuggingFace",
        "summary": "Paste a HuggingFace token (needed for gated models + private repos).",
        "category": "provider",
        "steps": [
            {"id": "intro", "type": "info", "title": "Connect HuggingFace",
             "body": "Required for downloading gated models like Llama / Gemma."},
            {"id": "install_sdk", "type": "action", "action": "pip_install",
             "packages": [{"pip": "huggingface_hub", "import": "huggingface_hub"}]},
            {"id": "open_tokens", "type": "open_url", "title": "Get a token",
             "url": "https://huggingface.co/settings/tokens",
             "body": "Create a token with at least 'Read' permission. For gated models, accept the license on each model page first."},
            {"id": "ask_token", "type": "input", "key": "hf_token", "title": "Paste the token", "secret": True,
             "placeholder": "hf_..."},
            {"id": "validate", "type": "action", "action": "validate_huggingface_token"},
            {"id": "save", "type": "save_env", "keys": {"HUGGING_FACE_HUB_TOKEN": "answers.hf_token"}},
            {"id": "done", "type": "summary", "title": "HuggingFace connected",
             "body": "Logged in as {result.validate.name}. Saved as HUGGING_FACE_HUB_TOKEN."},
        ],
    },

    # -----------------------------------------------------------------
    # CLOUD ACCOUNTS
    # -----------------------------------------------------------------

    "aws": {
        "title": "Connect AWS",
        "summary": "Sign in to AWS via access keys; validates via STS GetCallerIdentity.",
        "category": "cloud",
        "steps": [
            {"id": "intro", "type": "info", "title": "Connect AWS",
             "body": "We'll validate by calling sts:GetCallerIdentity (read-only) and save the keys to .env. For SSO setups, leave this wizard and run `aws configure sso` instead — see deploy/s3_store.md."},
            {"id": "open_iam", "type": "open_url", "title": "Get access keys",
             "url": "https://us-east-1.console.aws.amazon.com/iamv2/home#/users/create",
             "body": "Create an IAM user with programmatic access. Attach the policies you need (S3, ECR, etc.). Save the access key id + secret on the final screen — you can't view the secret again."},
            {"id": "ask_ak", "type": "input", "key": "access_key_id", "title": "AWS_ACCESS_KEY_ID",
             "placeholder": "AKIA..."},
            {"id": "ask_sk", "type": "input", "key": "secret_access_key", "title": "AWS_SECRET_ACCESS_KEY", "secret": True},
            {"id": "ask_region", "type": "input", "key": "region", "title": "Default region", "placeholder": "us-east-1", "default": "us-east-1"},
            {"id": "validate", "type": "action", "action": "validate_aws_keys"},
            {"id": "save", "type": "save_env", "keys": {
                "AWS_ACCESS_KEY_ID":     "answers.access_key_id",
                "AWS_SECRET_ACCESS_KEY": "answers.secret_access_key",
                "AWS_DEFAULT_REGION":    "answers.region",
            }},
            {"id": "done", "type": "summary", "title": "AWS connected",
             "body": "Signed in as {result.validate.arn}\nAccount: {result.validate.account}\nKeys saved to .env. Use deploy/s3_store.md to point AGCL at an S3 bucket."},
        ],
    },

    "gcp": {
        "title": "Connect Google Cloud",
        "summary": "Use a service-account JSON file. AGCL stores only the path.",
        "category": "cloud",
        "steps": [
            {"id": "intro", "type": "info", "title": "Connect Google Cloud",
             "body": "GCP recommends service accounts for unattended workloads. The JSON file lives on disk; we save its path in .env so AGCL restarts pick it up."},
            {"id": "open_console", "type": "open_url", "title": "Create a service account",
             "url": "https://console.cloud.google.com/iam-admin/serviceaccounts",
             "body": "Create an SA, grant the roles you need, then 'Manage keys' → 'Add key' → JSON. Save the downloaded file somewhere safe."},
            {"id": "ask_path", "type": "input", "key": "sa_path", "title": "Path to the JSON file",
             "placeholder": "/home/you/agcl-gcp.json"},
            {"id": "ask_project", "type": "input", "key": "project", "title": "Project ID (optional — read from JSON if blank)", "default": ""},
            {"id": "save", "type": "save_env", "keys": {
                "GOOGLE_APPLICATION_CREDENTIALS": "answers.sa_path",
                "GOOGLE_CLOUD_PROJECT":           "answers.project",
            }},
            {"id": "done", "type": "summary", "title": "Google Cloud connected",
             "body": "Path saved as GOOGLE_APPLICATION_CREDENTIALS.\nUse deploy/gcp.md to deploy AGCL to Cloud Run."},
        ],
    },

    "cloudflare": {
        "title": "Connect Cloudflare",
        "summary": "Paste an API token (verified) for Workers / R2 / DNS.",
        "category": "cloud",
        "steps": [
            {"id": "intro", "type": "info", "title": "Connect Cloudflare",
             "body": "We'll verify the token via Cloudflare's /user/tokens/verify and save it to .env. Use a least-privilege token scoped to the services you need (Workers, R2, etc.)."},
            {"id": "open_tokens", "type": "open_url", "title": "Create an API token",
             "url": "https://dash.cloudflare.com/profile/api-tokens",
             "body": "Click 'Create Token', pick the 'Edit Cloudflare Workers' or 'Custom Token' template, scope it to your account / zone, then 'Create Token'."},
            {"id": "ask_token", "type": "input", "key": "cf_token", "title": "Paste the token", "secret": True},
            {"id": "ask_relay", "type": "input", "key": "relay_url", "title": "Worker URL (optional — leave blank for now)", "placeholder": "https://agcl-relay.<sub>.workers.dev", "default": ""},
            {"id": "validate", "type": "action", "action": "validate_cloudflare_token"},
            {"id": "save", "type": "save_env", "keys": {
                "CLOUDFLARE_API_TOKEN": "answers.cf_token",
                "AGCL_RELAY_TOKEN":     "answers.cf_token",
                "AGCL_RELAY_URL":       "answers.relay_url",
            }},
            {"id": "done", "type": "summary", "title": "Cloudflare connected",
             "body": "Token verified — id {result.validate.id}.\nNext: deploy a Worker per deploy/cloudflare.md."},
        ],
    },

    "github": {
        "title": "Connect GitHub (for ghcr.io)",
        "summary": "Create a PAT, validate, log Docker in to ghcr.io.",
        "category": "cloud",
        "steps": [
            {"id": "intro", "type": "info", "title": "Connect GitHub",
             "body": "We'll create a Personal Access Token, validate it, then run `docker login ghcr.io` so you can push images to your container registry."},
            {"id": "open_tokens", "type": "open_url", "title": "Create a fine-grained PAT",
             "url": "https://github.com/settings/tokens/new?scopes=write:packages,read:packages&description=AGCL",
             "body": "Scopes pre-filled: write:packages + read:packages. Set a sensible expiration. Copy the token now — you can't see it again."},
            {"id": "ask_token", "type": "input", "key": "gh_token", "title": "Paste the PAT", "secret": True,
             "placeholder": "ghp_... or github_pat_..."},
            {"id": "validate", "type": "action", "action": "validate_github_pat"},
            {"id": "docker_login", "type": "action", "action": "docker_login_ghcr"},
            {"id": "save", "type": "save_env", "keys": {
                "GITHUB_USERNAME":         "result.validate.login",
                "GITHUB_PERSONAL_TOKEN":   "answers.gh_token",
                "DOCKER_REGISTRY":         "ghcr.io",
                "DOCKER_USERNAME":         "result.validate.login",
            }},
            {"id": "done", "type": "summary", "title": "GitHub + ghcr.io connected",
             "body": "Logged in as {result.validate.login}. You can now `docker push ghcr.io/{result.validate.login}/agcl-node:tag`."},
        ],
    },

    # -----------------------------------------------------------------
    # BOTS
    # -----------------------------------------------------------------

    "discord": {
        "title": "Deploy a Discord bot",
        "summary": "Create a bot app, paste the token, generate an invite URL, start the bot.",
        "category": "bot",
        "steps": [
            {"id": "intro", "type": "info", "title": "Deploy a Discord bot",
             "body": "1) Create an application in Discord's dev portal.\n2) Paste the bot token here.\n3) We generate an invite URL.\n4) Click it, pick your server, authorize.\n5) Run `agcl toolkit discord` to bring the bot online."},
            {"id": "install_sdk", "type": "action", "action": "pip_install",
             "packages": [{"pip": "discord.py", "import": "discord"}]},
            {"id": "open_portal", "type": "open_url", "title": "Create an application",
             "url": "https://discord.com/developers/applications",
             "body": "Click 'New Application'. On the next page, sidebar → 'Bot' → 'Reset Token' → copy the token. Also enable 'MESSAGE CONTENT INTENT' down the page so @mentions work."},
            {"id": "ask_token", "type": "input", "key": "bot_token", "title": "Bot token", "secret": True,
             "placeholder": "MTIzNDU2Nzg5..."},
            {"id": "validate", "type": "action", "action": "validate_discord_token", "token_key": "bot_token"},
            {"id": "ask_app_id", "type": "input", "key": "app_id",
             "title": "Application ID (top of the General Information page)",
             "default": "{result.validate.id}"},
            {"id": "ask_guild", "type": "input", "key": "guild_id",
             "title": "Test guild ID (optional — makes slash commands instant)", "default": "",
             "placeholder": "right-click your server → Copy Server ID (Developer Mode on)"},
            {"id": "save", "type": "save_env", "keys": {
                "DISCORD_BOT_TOKEN":  "answers.bot_token",
                "DISCORD_APP_ID":     "answers.app_id",
                "DISCORD_GUILD_ID":   "answers.guild_id",
            }},
            {"id": "invite", "type": "action", "action": "discord_invite_url"},
            {"id": "open_invite", "type": "open_url", "title": "Invite the bot to your server",
             "url": "{result.invite.invite_url}",
             "body": "Pick the server, authorize. The bot joins but is offline until you start it."},
            {"id": "done", "type": "summary", "title": "Discord bot ready",
             "body": "Bot user: {result.validate.username} (id {result.validate.id})\nInvite URL: {result.invite.invite_url}\n\nStart the gateway with:\n    python main.py toolkit discord\nThen type `/ask hello` in any channel the bot can see."},
        ],
    },

    "slack": {
        "title": "Deploy a Slack bot",
        "summary": "Paste a Bolt bot token + signing secret; AGCL runs the adapter.",
        "category": "bot",
        "steps": [
            {"id": "intro", "type": "info", "title": "Deploy a Slack bot",
             "body": "Slack apps need a bot token (xoxb-...) and a signing secret. Both come from api.slack.com under your app's settings."},
            {"id": "install_sdk", "type": "action", "action": "pip_install",
             "packages": [{"pip": "slack-bolt", "import": "slack_bolt"}]},
            {"id": "open_slack", "type": "open_url", "title": "Create a Slack app",
             "url": "https://api.slack.com/apps?new_app=1",
             "body": "Pick 'From scratch', name + workspace.\n- 'OAuth & Permissions' → add bot scopes: chat:write, app_mentions:read, commands.\n- 'Install to Workspace' → copy the Bot User OAuth Token (xoxb-...).\n- 'Basic Information' → copy the Signing Secret."},
            {"id": "ask_bot_token", "type": "input", "key": "slack_bot_token", "title": "SLACK_BOT_TOKEN", "secret": True, "placeholder": "xoxb-..."},
            {"id": "ask_signing", "type": "input", "key": "slack_signing_secret", "title": "SLACK_SIGNING_SECRET", "secret": True},
            {"id": "save", "type": "save_env", "keys": {
                "SLACK_BOT_TOKEN":      "answers.slack_bot_token",
                "SLACK_SIGNING_SECRET": "answers.slack_signing_secret",
            }},
            {"id": "done", "type": "summary", "title": "Slack bot ready",
             "body": "Tokens saved. Start the adapter with:\n    python main.py slack --http --port 3000\n\nThen point Slack's slash command + event subscription URLs at https://<your-host>:3000/slack/events."},
        ],
    },

    # -----------------------------------------------------------------
    # LOCAL MODEL SERVERS (one-click Docker)
    # -----------------------------------------------------------------

    "ollama": {
        "title": "Set up Ollama (local)",
        "summary": "Install + pull a model + wire AGCL.",
        "category": "model",
        "steps": [
            {"id": "intro", "type": "info", "title": "Set up Ollama",
             "body": "We'll pull a small chat model and tell AGCL to use it. Ollama must already be installed (https://ollama.com/download)."},
            {"id": "choose_model", "type": "choice", "key": "model", "title": "Pick a starter model",
             "options": [
                 {"value": "llama3.2:1b",  "label": "llama3.2:1b — tiny (~1 GB), runs anywhere"},
                 {"value": "llama3.2:3b",  "label": "llama3.2:3b — small (~2 GB)"},
                 {"value": "llama3.1:8b",  "label": "llama3.1:8b — medium (~5 GB), GPU recommended"},
                 {"value": "qwen2.5:7b",   "label": "qwen2.5:7b — strong general model"},
             ]},
            {"id": "pull", "type": "action", "action": "ollama_pull"},
            {"id": "save", "type": "save_env", "keys": {
                "OLLAMA_HOST":  "http://localhost:11434",
                "OLLAMA_MODEL": "answers.model",
            }},
            {"id": "ping", "type": "action", "action": "ping_url", "url": "http://localhost:11434/api/tags"},
            {"id": "done", "type": "summary", "title": "Ollama is ready",
             "body": "Pulled {answers.model}. AGCL will now route chats through Ollama by default.\nVerify: Toolkit tab → ping `ollama`."},
        ],
    },

    "vllm": {
        "title": "Set up vLLM (Docker)",
        "summary": "Spin up vLLM as an OpenAI-compatible server in a container.",
        "category": "model",
        "steps": [
            {"id": "intro", "type": "info", "title": "Set up vLLM",
             "body": "We'll start a Docker container running vLLM. Needs an NVIDIA GPU + the nvidia-container-toolkit installed (deploy/vllm.md has prereqs)."},
            {"id": "ask_model", "type": "input", "key": "model", "title": "HuggingFace model id",
             "default": "Qwen/Qwen2.5-7B-Instruct"},
            {"id": "ask_port", "type": "input", "key": "port", "title": "Host port", "default": "8000"},
            {"id": "ask_maxlen", "type": "input", "key": "max_len", "title": "Max model context length", "default": "16384"},
            {"id": "run", "type": "action", "action": "docker_run_detached",
             "name": "vllm",
             "cmd": ["docker", "run", "-d", "--name", "vllm",
                     "--runtime", "nvidia", "--gpus", "all",
                     "-p", "{answers.port}:8000",
                     "--shm-size=4g",
                     "-v", "vllm-hf-cache:/root/.cache/huggingface",
                     "-e", "HUGGING_FACE_HUB_TOKEN",
                     "vllm/vllm-openai:latest",
                     "--model", "{answers.model}",
                     "--max-model-len", "{answers.max_len}"]},
            {"id": "wait", "type": "wait_for", "url": "http://localhost:{answers.port}/v1/models", "timeout": 600, "interval": 5},
            {"id": "save", "type": "save_env", "keys": {
                "VLLM_HOST":  "http://localhost:{answers.port}",
                "VLLM_MODEL": "answers.model",
                "VLLM_API_KEY": "EMPTY",
            }},
            {"id": "done", "type": "summary", "title": "vLLM is up",
             "body": "Container `vllm` running on port {answers.port}, serving {answers.model}.\nFollow logs: `docker logs -f vllm`.\nStop: `docker rm -f vllm`."},
        ],
    },

    "tgi": {
        "title": "Set up TGI (Docker)",
        "summary": "Spin up HuggingFace Text-Generation-Inference. Note: maintenance mode — prefer vLLM.",
        "category": "model",
        "steps": [
            {"id": "intro", "type": "info", "title": "Set up TGI",
             "body": "TGI is in maintenance mode. Run it only for compatibility; new deploys should use vLLM."},
            {"id": "ask_model", "type": "input", "key": "model", "title": "HuggingFace model id",
             "default": "mistralai/Mistral-7B-Instruct-v0.3"},
            {"id": "ask_port", "type": "input", "key": "port", "title": "Host port", "default": "8080"},
            {"id": "run", "type": "action", "action": "docker_run_detached",
             "name": "tgi",
             "cmd": ["docker", "run", "-d", "--name", "tgi",
                     "--gpus", "all",
                     "-p", "{answers.port}:80",
                     "--shm-size=1g",
                     "-e", "HUGGING_FACE_HUB_TOKEN",
                     "-v", "tgi-data:/data",
                     "ghcr.io/huggingface/text-generation-inference:latest",
                     "--model-id", "{answers.model}"]},
            {"id": "wait", "type": "wait_for", "url": "http://localhost:{answers.port}/health", "timeout": 600, "interval": 5},
            {"id": "save", "type": "save_env", "keys": {
                "TGI_HOST": "http://localhost:{answers.port}",
            }},
            {"id": "done", "type": "summary", "title": "TGI is up",
             "body": "Container `tgi` running on port {answers.port}, serving {answers.model}."},
        ],
    },

    "litellm": {
        "title": "Set up LiteLLM proxy (Docker)",
        "summary": "Run a LiteLLM gateway and tell AGCL to use it.",
        "category": "infra",
        "steps": [
            {"id": "intro", "type": "info", "title": "Set up LiteLLM",
             "body": "LiteLLM proxies many providers behind one OpenAI-compatible endpoint. We'll start it without a DB (no spend tracking) for the quickstart. Add Postgres later — see deploy/litellm_gw.md."},
            {"id": "ask_port", "type": "input", "key": "port", "title": "Host port", "default": "4000"},
            {"id": "ask_master", "type": "input", "key": "master_key", "title": "Master key (will be saved)",
             "secret": True, "default": "sk-master-paste-your-own"},
            {"id": "run", "type": "action", "action": "docker_run_detached",
             "name": "litellm",
             "cmd": ["docker", "run", "-d", "--name", "litellm",
                     "-p", "{answers.port}:4000",
                     "-e", "LITELLM_MASTER_KEY={answers.master_key}",
                     "-e", "OPENAI_API_KEY",
                     "-e", "ANTHROPIC_API_KEY",
                     "ghcr.io/berriai/litellm:main-latest", "--port", "4000"]},
            {"id": "wait", "type": "wait_for", "url": "http://localhost:{answers.port}/health/readiness", "timeout": 120, "interval": 3},
            {"id": "save", "type": "save_env", "keys": {
                "AGCL_LLM_BASE_URL": "http://localhost:{answers.port}",
                "AGCL_LLM_API_KEY":  "answers.master_key",
                "AGCL_LLM_MODEL":    "gpt-4o",
            }},
            {"id": "done", "type": "summary", "title": "LiteLLM proxy is up",
             "body": "AGCL_LLM_BASE_URL points at the proxy. From now on chat traffic flows through it. Add a `model_list` config (deploy/litellm_gw.md) and restart the container to enable routing."},
        ],
    },

    # -----------------------------------------------------------------
    # STORES
    # -----------------------------------------------------------------

    "redis": {
        "title": "Set up Redis (Docker)",
        "summary": "Run Redis locally and tell AGCL to use it for distributed state.",
        "category": "store",
        "steps": [
            {"id": "intro", "type": "info", "title": "Set up Redis",
             "body": "Required only if you'll run multiple AGCL nodes. Otherwise local FS is fine — but this also makes sessions survive node restarts cleanly."},
            {"id": "install_sdk", "type": "action", "action": "pip_install",
             "packages": [{"pip": "redis[hiredis]", "import": "redis"}]},
            {"id": "ask_port", "type": "input", "key": "port", "title": "Host port", "default": "6379"},
            {"id": "ask_pw",   "type": "input", "key": "password", "title": "Password (blank = none)", "secret": True, "default": ""},
            {"id": "run",      "type": "action", "action": "docker_run_detached",
             "name": "redis",
             "cmd": ["docker", "run", "-d", "--name", "redis",
                     "-p", "{answers.port}:6379",
                     "-v", "redis-data:/data",
                     "redis:7-alpine", "redis-server", "--appendonly", "yes"]},
            {"id": "wait", "type": "wait_for", "url": "http://localhost:{answers.port}/", "timeout": 30, "interval": 1},
            {"id": "save", "type": "save_env", "keys": {
                "AGCL_REDIS_URL": "redis://localhost:{answers.port}",
            }},
            {"id": "done", "type": "summary", "title": "Redis is up",
             "body": "Container `redis` running on port {answers.port}. AGCL will use it next time the node starts (state-store factory reads AGCL_REDIS_URL at startup)."},
        ],
    },

    "minio": {
        "title": "Set up MinIO (Docker, S3 checkpoint store)",
        "summary": "Run MinIO locally as the S3 backend for trained-topic checkpoints.",
        "category": "store",
        "steps": [
            {"id": "intro", "type": "info", "title": "Set up MinIO",
             "body": "MinIO is a self-hosted S3-compatible server. We'll start it on ports 9000 (API) + 9001 (console), then create a bucket via the console UI."},
            {"id": "install_sdk", "type": "action", "action": "pip_install",
             "packages": [{"pip": "aioboto3", "import": "aioboto3"}]},
            {"id": "ask_user", "type": "input", "key": "minio_user", "title": "Root username", "default": "minioadmin"},
            {"id": "ask_pw",   "type": "input", "key": "minio_pw",   "title": "Root password (≥8 chars)", "secret": True, "default": ""},
            {"id": "ask_bucket", "type": "input", "key": "bucket", "title": "Bucket name to create later", "default": "agcl-checkpoints"},
            {"id": "run", "type": "action", "action": "docker_run_detached",
             "name": "minio",
             "cmd": ["docker", "run", "-d", "--name", "minio",
                     "-p", "9000:9000", "-p", "9001:9001",
                     "-e", "MINIO_ROOT_USER={answers.minio_user}",
                     "-e", "MINIO_ROOT_PASSWORD={answers.minio_pw}",
                     "-v", "minio-data:/data",
                     "quay.io/minio/minio", "server", "/data", "--console-address", ":9001"]},
            {"id": "wait", "type": "wait_for", "url": "http://localhost:9001/", "timeout": 30, "interval": 1},
            {"id": "open_console", "type": "open_url", "title": "Open MinIO console to create the bucket",
             "url": "http://localhost:9001",
             "body": "Sign in with the credentials you just chose. Buckets → Create Bucket → `{answers.bucket}`. Then 'Access Keys' → Create new (will be used by AGCL)."},
            {"id": "ask_ak", "type": "input", "key": "access_key", "title": "Bucket access key id"},
            {"id": "ask_sk", "type": "input", "key": "secret_key", "title": "Bucket secret access key", "secret": True},
            {"id": "save", "type": "save_env", "keys": {
                "AGCL_S3_BUCKET":   "answers.bucket",
                "AGCL_S3_ENDPOINT": "http://localhost:9000",
                "AGCL_S3_KEY_ID":   "answers.access_key",
                "AGCL_S3_SECRET":   "answers.secret_key",
                "AGCL_S3_REGION":   "us-east-1",
            }},
            {"id": "done", "type": "summary", "title": "MinIO is up",
             "body": "Bucket `{answers.bucket}` on http://localhost:9000. Restart AGCL — `Toolkit → ping s3` will now report `kind: s3`."},
        ],
    },

    "postgres": {
        "title": "Set up Postgres (Docker, for LiteLLM spend tracking)",
        "summary": "Run a Postgres container and emit a DATABASE_URL.",
        "category": "store",
        "steps": [
            {"id": "intro", "type": "info", "title": "Set up Postgres",
             "body": "Only needed if you want LiteLLM's spend tracking + admin UI. Skip if you just want routing."},
            {"id": "ask_pw", "type": "input", "key": "pg_pw", "title": "Postgres password", "secret": True, "default": "litellmpass"},
            {"id": "ask_db", "type": "input", "key": "pg_db", "title": "Database name", "default": "litellm"},
            {"id": "run", "type": "action", "action": "docker_run_detached",
             "name": "litellm-db",
             "cmd": ["docker", "run", "-d", "--name", "litellm-db",
                     "-p", "5432:5432",
                     "-e", "POSTGRES_PASSWORD={answers.pg_pw}",
                     "-e", "POSTGRES_DB={answers.pg_db}",
                     "-v", "litellm-db:/var/lib/postgresql/data",
                     "postgres:16"]},
            {"id": "wait", "type": "wait_for", "url": "http://localhost:5432/", "timeout": 30, "interval": 2},
            {"id": "save", "type": "save_env", "keys": {
                "DATABASE_URL": "postgresql://postgres:{answers.pg_pw}@localhost:5432/{answers.pg_db}",
            }},
            {"id": "done", "type": "summary", "title": "Postgres is up",
             "body": "DATABASE_URL written to .env. Restart the LiteLLM container — it'll pick up the DB and expose the admin UI at /ui."},
        ],
    },

    # -----------------------------------------------------------------
    # PROVIDERS — generic OpenAI-compatible custom-provider registration
    # -----------------------------------------------------------------

    "openai_compat": {
        "title": "Register a custom OpenAI-compatible provider",
        "summary": "Together / Fireworks / Groq / DeepSeek / any local server.",
        "category": "provider",
        "steps": [
            {"id": "intro", "type": "info", "title": "Register a custom provider",
             "body": "Anything that speaks /v1/chat/completions works. You'll need the base URL, an env var holding the key, a model id, and a name."},
            {"id": "ask_name", "type": "input", "key": "name", "title": "Name (used in the chat dropdown)", "placeholder": "together"},
            {"id": "ask_base", "type": "input", "key": "base_url", "title": "Base URL ending in /v1",
             "placeholder": "https://api.together.xyz/v1"},
            {"id": "ask_keyenv", "type": "input", "key": "api_key_env",
             "title": "Env var holding the API key (e.g. TOGETHER_API_KEY)"},
            {"id": "ask_model", "type": "input", "key": "model", "title": "Model id"},
            {"id": "ask_kind",  "type": "choice", "key": "kind", "title": "Use it for",
             "options": [{"value": "chat", "label": "chat replies"},
                         {"value": "knowledge", "label": "knowledge formation (MAS bootstrap)"}]},
            {"id": "register", "type": "action", "action": "register_custom_provider"},
            {"id": "done", "type": "summary", "title": "Provider registered",
             "body": "`{answers.name}` is now selectable from the Chat tab dropdown. Set a quota in Usage & Cost → Quotas if you're worried about spend."},
        ],
    },

    "webrtc": {
        "title": "Enable WebRTC (peer-to-peer streaming)",
        "summary": "Turn on signaling, set STUN/TURN.",
        "category": "infra",
        "steps": [
            {"id": "intro", "type": "info", "title": "Enable WebRTC signaling",
             "body": "We'll flip on signaling and set ICE servers. The Python aiortc peer is a separate process — see deploy/webrtc.md."},
            {"id": "install_sdk", "type": "action", "action": "pip_install",
             "packages": [{"pip": "aiortc", "import": "aiortc"}]},
            {"id": "ask_stun", "type": "input", "key": "stun_url", "title": "STUN URL", "default": "stun:stun.l.google.com:19302"},
            {"id": "ask_turn", "type": "input", "key": "turn_url", "title": "TURN URL (optional)", "default": ""},
            {"id": "ask_tu",   "type": "input", "key": "turn_user", "title": "TURN username (if TURN set)", "default": ""},
            {"id": "ask_tp",   "type": "input", "key": "turn_pw", "title": "TURN password (if TURN set)", "secret": True, "default": ""},
            {"id": "save", "type": "save_env", "keys": {
                "AGCL_WEBRTC_ENABLED": "1",
                "AGCL_STUN_URL":       "answers.stun_url",
                "AGCL_TURN_URL":       "answers.turn_url",
                "AGCL_TURN_USERNAME":  "answers.turn_user",
                "AGCL_TURN_PASSWORD":  "answers.turn_pw",
            }},
            {"id": "done", "type": "summary", "title": "WebRTC enabled",
             "body": "Signaling is on. Reload .env in Setup → Cloud providers, then verify with `GET /node/toolkit/webrtc/status`."},
        ],
    },

    # -----------------------------------------------------------------
    # KUBERNETES
    # -----------------------------------------------------------------

    "k8s": {
        "title": "Point AGCL at a Kubernetes cluster",
        "summary": "Save KUBECONFIG path + switch context.",
        "category": "infra",
        "steps": [
            {"id": "intro", "type": "info", "title": "Connect Kubernetes",
             "body": "We'll save the path to your kubeconfig in .env. AGCL's manifest emitter (Deploy tab → Emit Kubernetes) will then know where to look."},
            {"id": "ask_path", "type": "input", "key": "kubeconfig_path", "title": "Kubeconfig path",
             "default": "~/.kube/config"},
            {"id": "ask_ctx",  "type": "input", "key": "context", "title": "Context to switch to (optional)", "default": ""},
            {"id": "save", "type": "save_env", "keys": {"KUBECONFIG": "answers.kubeconfig_path"}},
            {"id": "done", "type": "summary", "title": "Kubernetes wired",
             "body": "KUBECONFIG=`{answers.kubeconfig_path}`. Run `python main.py auth k8s-status` to confirm cluster reachability, then Deploy tab → Emit Kubernetes."},
        ],
    },
}


def get_wizard(name: str) -> Optional[Dict[str, Any]]:
    return WIZARDS.get(name)


def list_wizards() -> List[Dict[str, Any]]:
    return [{"name": k, "title": v["title"], "summary": v["summary"], "category": v.get("category", "")}
            for k, v in WIZARDS.items()]
