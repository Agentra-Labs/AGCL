"""
agcl.integrations_auth — real auth + status for cloud providers and ops services.

Two responsibilities:

1) PROVIDER LIVE CHECKS — verify an API key works by hitting the
   provider's models endpoint, and fetch quotas/balance where the
   provider exposes them.

2) OPS-SERVICE AUTH — Docker registries, Google Cloud, Kubernetes.
   Saves *references* to credentials in .env (file paths, registry
   names, project ids) so reloading the node automatically re-uses
   them. Raw passwords/keys live in their canonical stores
   (~/.docker/config.json, the service-account JSON file you point
   at, your kubeconfig) — never in .env.

Every function returns a JSON-friendly dict shaped roughly:
    {ok: bool, status: "ok"|"warn"|"error", message: str, details: {...}}

No secrets are returned. Keys passed in for checking are validated by
making the call, then forgotten.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


# ----------------------------------------------------------------------
# Tiny HTTP helper
# ----------------------------------------------------------------------

def _http(url: str, *, method: str = "GET",
          headers: Optional[Dict[str, str]] = None,
          body: Optional[bytes] = None,
          timeout: float = 10.0) -> Dict[str, Any]:
    """Lightweight HTTP via stdlib. Returns {status, text, json|None, error?}."""
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            try:    parsed = json.loads(text) if text else None
            except Exception: parsed = None
            return {"status": resp.status, "text": text, "json": parsed}
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        try:    parsed = json.loads(text) if text else None
        except Exception: parsed = None
        return {"status": e.code, "text": text, "json": parsed,
                "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"status": 0, "text": "", "json": None, "error": f"{type(e).__name__}: {e}"}


# ----------------------------------------------------------------------
# Provider live checks
# ----------------------------------------------------------------------

def check_openai_key(key: Optional[str] = None) -> Dict[str, Any]:
    """Verify an OpenAI key by listing models."""
    key = key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return {"ok": False, "status": "error", "message": "no key configured"}
    r = _http("https://api.openai.com/v1/models",
              headers={"Authorization": f"Bearer {key}"})
    if r.get("status") == 200:
        models = (r.get("json") or {}).get("data") or []
        return {"ok": True, "status": "ok",
                "message": f"key valid — {len(models)} models accessible",
                "details": {"models_count": len(models)}}
    return {"ok": False, "status": "error",
            "message": r.get("error") or f"HTTP {r.get('status')}",
            "details": {"status": r.get("status"), "body": (r.get("text") or "")[:200]}}


def check_anthropic_key(key: Optional[str] = None) -> Dict[str, Any]:
    """Verify an Anthropic key by listing models (anthropic-version: 2023-06-01)."""
    key = key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return {"ok": False, "status": "error", "message": "no key configured"}
    r = _http("https://api.anthropic.com/v1/models",
              headers={
                  "x-api-key": key,
                  "anthropic-version": "2023-06-01",
              })
    if r.get("status") == 200:
        models = (r.get("json") or {}).get("data") or []
        return {"ok": True, "status": "ok",
                "message": f"key valid — {len(models)} models accessible",
                "details": {"models_count": len(models)}}
    return {"ok": False, "status": "error",
            "message": r.get("error") or f"HTTP {r.get('status')}",
            "details": {"status": r.get("status"), "body": (r.get("text") or "")[:200]}}


def check_deepseek_key(key: Optional[str] = None) -> Dict[str, Any]:
    """Verify a DeepSeek key by listing models (OpenAI-compatible endpoint)."""
    key = key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return {"ok": False, "status": "error", "message": "no key configured"}
    r = _http("https://api.deepseek.com/v1/models",
              headers={"Authorization": f"Bearer {key}"})
    if r.get("status") == 200:
        models = (r.get("json") or {}).get("data") or []
        return {"ok": True, "status": "ok",
                "message": f"key valid — {len(models)} models accessible",
                "details": {"models_count": len(models)}}
    return {"ok": False, "status": "error",
            "message": r.get("error") or f"HTTP {r.get('status')}",
            "details": {"status": r.get("status"), "body": (r.get("text") or "")[:200]}}


def fetch_openai_quota(key: Optional[str] = None) -> Dict[str, Any]:
    """OpenAI's billing endpoints have been deprecated multiple times.
    We try the legacy dashboard endpoints and degrade gracefully — many
    accounts now require an Admin API key for /organization/usage/*."""
    key = key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return {"ok": False, "available": False, "message": "no key"}
    # Legacy credit-grants endpoint — works for some accounts
    r = _http("https://api.openai.com/dashboard/billing/credit_grants",
              headers={"Authorization": f"Bearer {key}"})
    if r.get("status") == 200 and r.get("json"):
        j = r["json"]
        return {"ok": True, "available": True,
                "currency": "usd",
                "total_granted":  j.get("total_granted"),
                "total_used":     j.get("total_used"),
                "total_available": j.get("total_available"),
                "source": "/dashboard/billing/credit_grants"}
    return {"ok": False, "available": False,
            "message": "OpenAI does not expose quota for this key class (needs Admin API key)",
            "hint": "Use the dashboard at platform.openai.com/usage"}


def fetch_anthropic_quota(key: Optional[str] = None) -> Dict[str, Any]:
    """Anthropic doesn't expose a quota/balance endpoint on regular API keys.
    Admin-API keys can hit /v1/organizations/usage_report but that's
    a separate product. For regular keys, we report 'unavailable' with
    a pointer to the dashboard."""
    key = key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return {"ok": False, "available": False, "message": "no key"}
    return {"ok": False, "available": False,
            "message": "Anthropic does not expose quota via regular API keys.",
            "hint": "See console.anthropic.com/settings/billing"}


def fetch_deepseek_balance(key: Optional[str] = None) -> Dict[str, Any]:
    """DeepSeek exposes /user/balance — a list of currency balances."""
    key = key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return {"ok": False, "available": False, "message": "no key"}
    r = _http("https://api.deepseek.com/user/balance",
              headers={"Authorization": f"Bearer {key}"})
    if r.get("status") == 200 and r.get("json"):
        j = r["json"]
        balances = j.get("balance_infos") or []
        return {"ok": True, "available": True,
                "is_available": j.get("is_available"),
                "balances": [
                    {"currency": b.get("currency"),
                     "total_balance":     b.get("total_balance"),
                     "granted_balance":   b.get("granted_balance"),
                     "topped_up_balance": b.get("topped_up_balance")}
                    for b in balances
                ],
                "source": "/user/balance"}
    return {"ok": False, "available": False,
            "message": r.get("error") or f"HTTP {r.get('status')}"}


def check_all_providers() -> Dict[str, Any]:
    """Run every check + quota fetch in one shot for the GUI."""
    out: Dict[str, Any] = {"providers": {}}
    out["providers"]["openai"]   = {**check_openai_key(),   "quota": fetch_openai_quota()}
    out["providers"]["anthropic"] = {**check_anthropic_key(), "quota": fetch_anthropic_quota()}
    out["providers"]["deepseek"] = {**check_deepseek_key(), "quota": fetch_deepseek_balance()}
    return out


# ----------------------------------------------------------------------
# Docker
# ----------------------------------------------------------------------

def _run(cmd: List[str], *, input_str: Optional[str] = None,
         timeout: float = 30.0) -> Dict[str, Any]:
    if not shutil.which(cmd[0]):
        return {"ok": False, "missing": True, "message": f"`{cmd[0]}` not on PATH"}
    try:
        p = subprocess.run(cmd, input=input_str, text=True, capture_output=True,
                           timeout=timeout)
        return {
            "ok": p.returncode == 0,
            "returncode": p.returncode,
            "stdout": (p.stdout or "").strip(),
            "stderr": (p.stderr or "").strip(),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "command timed out"}
    except Exception as e:
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


def docker_status() -> Dict[str, Any]:
    """Is Docker installed? Is the daemon reachable? Which registries are logged in?"""
    if not shutil.which("docker"):
        return {"ok": False, "installed": False,
                "message": "`docker` not on PATH — install Docker Desktop / Engine"}
    info = _run(["docker", "info", "--format", "{{json .}}"], timeout=5.0)
    daemon_ok = info["ok"]
    auths: List[str] = []
    cfg_path = Path(os.path.expanduser("~/.docker/config.json"))
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
            auths = list((cfg.get("auths") or {}).keys())
        except Exception: pass
    return {
        "ok": daemon_ok,
        "installed": True,
        "daemon_running": daemon_ok,
        "message": info["stderr"] if not daemon_ok else "daemon reachable",
        "registries_logged_in": auths,
        "config_path": str(cfg_path),
        "env_registry": os.environ.get("DOCKER_REGISTRY", ""),
        "env_username": os.environ.get("DOCKER_USERNAME", ""),
    }


def docker_login(registry: str, username: str, password: str,
                 persist_reference: bool = True) -> Dict[str, Any]:
    """Run `docker login`. Password is piped via stdin — never on cmdline.
    Saves DOCKER_REGISTRY + DOCKER_USERNAME to .env (no password) so subsequent
    process restarts know which registry to ping for status."""
    if not registry or not username or not password:
        return {"ok": False, "message": "registry, username and password required"}
    cmd = ["docker", "login", registry, "--username", username, "--password-stdin"]
    res = _run(cmd, input_str=password, timeout=20.0)
    if not res["ok"]:
        return {"ok": False,
                "message": "docker login failed",
                "stderr": res.get("stderr") or res.get("message"),
                "returncode": res.get("returncode")}
    if persist_reference:
        from agcl.setup import _patch_env_file
        _patch_env_file({
            "DOCKER_REGISTRY": registry,
            "DOCKER_USERNAME": username,
        })
    return {"ok": True,
            "message": f"logged in to {registry} as {username}",
            "registry": registry,
            "username": username,
            "persisted": persist_reference}


def docker_logout(registry: str) -> Dict[str, Any]:
    if not registry:
        return {"ok": False, "message": "registry required"}
    res = _run(["docker", "logout", registry], timeout=10.0)
    return {"ok": res["ok"], "stdout": res.get("stdout"), "stderr": res.get("stderr")}


# ----------------------------------------------------------------------
# Google Cloud
# ----------------------------------------------------------------------

def gcp_status() -> Dict[str, Any]:
    """Check ADC env var, service-account file, optional gcloud accounts."""
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    creds_ok = bool(creds) and Path(os.path.expanduser(creds)).exists()
    sa_info: Optional[Dict[str, Any]] = None
    if creds_ok:
        try:
            j = json.loads(Path(os.path.expanduser(creds)).read_text())
            sa_info = {
                "type":           j.get("type"),
                "project_id":     j.get("project_id"),
                "client_email":   j.get("client_email"),
                "private_key_id": j.get("private_key_id"),
            }
        except Exception as e:
            sa_info = {"error": f"could not parse service-account JSON: {e}"}

    gcloud_ok = bool(shutil.which("gcloud"))
    gcloud_accounts: List[str] = []
    gcloud_project = ""
    if gcloud_ok:
        r = _run(["gcloud", "auth", "list", "--format=value(account)"], timeout=5.0)
        if r["ok"]:
            gcloud_accounts = [line for line in r["stdout"].splitlines() if line.strip()]
        rp = _run(["gcloud", "config", "get-value", "project"], timeout=5.0)
        if rp["ok"]:
            gcloud_project = rp["stdout"].strip().splitlines()[0] if rp["stdout"] else ""

    return {
        "ok": creds_ok or bool(gcloud_accounts),
        "service_account_path": creds,
        "service_account_loaded": creds_ok,
        "service_account": sa_info,
        "project": project or (sa_info or {}).get("project_id", "") or gcloud_project,
        "gcloud_installed": gcloud_ok,
        "gcloud_accounts": gcloud_accounts,
        "gcloud_project": gcloud_project,
    }


def gcp_set_credentials(service_account_path: str,
                        project: Optional[str] = None) -> Dict[str, Any]:
    """Save GOOGLE_APPLICATION_CREDENTIALS (and optionally GOOGLE_CLOUD_PROJECT)
    to .env. Validates the file exists and parses as JSON."""
    if not service_account_path:
        return {"ok": False, "message": "service_account_path required"}
    p = Path(os.path.expanduser(service_account_path))
    if not p.exists():
        return {"ok": False, "message": f"file not found: {p}"}
    try:
        j = json.loads(p.read_text())
    except Exception as e:
        return {"ok": False, "message": f"not valid JSON: {e}"}
    if not project:
        project = j.get("project_id", "")
    updates = {"GOOGLE_APPLICATION_CREDENTIALS": str(p)}
    if project:
        updates["GOOGLE_CLOUD_PROJECT"] = project
    from agcl.setup import _patch_env_file
    _patch_env_file(updates)
    # Also reflect into live os.environ so the change takes effect immediately.
    for k, v in updates.items():
        os.environ[k] = v
    return {"ok": True, "applied": list(updates.keys()),
            "service_account_path": str(p),
            "project": project,
            "client_email": j.get("client_email")}


def gcp_clear_credentials() -> Dict[str, Any]:
    from agcl.setup import _patch_env_file
    _patch_env_file({"GOOGLE_APPLICATION_CREDENTIALS": "",
                     "GOOGLE_CLOUD_PROJECT": ""})
    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
        os.environ.pop(k, None)
    return {"ok": True, "cleared": ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"]}


# ----------------------------------------------------------------------
# Kubernetes
# ----------------------------------------------------------------------

def k8s_status() -> Dict[str, Any]:
    """Check KUBECONFIG, kubectl, and whether cluster-info reaches a cluster."""
    kc = os.environ.get("KUBECONFIG", "") or os.path.expanduser("~/.kube/config")
    kc_exists = Path(os.path.expanduser(kc)).exists()
    kubectl_ok = bool(shutil.which("kubectl"))

    current_ctx = ""
    reachable = False
    namespaces: List[str] = []
    err = ""
    if kubectl_ok:
        r = _run(["kubectl", "config", "current-context"], timeout=5.0)
        if r["ok"]:
            current_ctx = r["stdout"].strip().splitlines()[0] if r["stdout"] else ""
        cl = _run(["kubectl", "cluster-info"], timeout=5.0)
        reachable = cl["ok"]
        if not reachable:
            err = (cl.get("stderr") or cl.get("message") or "").splitlines()[0] if (cl.get("stderr") or cl.get("message")) else ""
        if reachable:
            nr = _run(["kubectl", "get", "ns", "-o", "name"], timeout=5.0)
            if nr["ok"]:
                namespaces = [n.split("/", 1)[-1] for n in nr["stdout"].splitlines()]
    return {
        "ok": reachable,
        "kubeconfig": kc,
        "kubeconfig_exists": kc_exists,
        "kubectl_installed": kubectl_ok,
        "current_context": current_ctx,
        "cluster_reachable": reachable,
        "namespaces": namespaces[:50],
        "error": err,
    }


def k8s_set_kubeconfig(kubeconfig_path: str,
                      context: Optional[str] = None) -> Dict[str, Any]:
    if not kubeconfig_path:
        return {"ok": False, "message": "kubeconfig_path required"}
    p = Path(os.path.expanduser(kubeconfig_path))
    if not p.exists():
        return {"ok": False, "message": f"file not found: {p}"}
    updates = {"KUBECONFIG": str(p)}
    from agcl.setup import _patch_env_file
    _patch_env_file(updates)
    os.environ["KUBECONFIG"] = str(p)
    if context and shutil.which("kubectl"):
        r = _run(["kubectl", "config", "use-context", context], timeout=5.0)
        if not r["ok"]:
            return {"ok": False, "kubeconfig": str(p),
                    "message": f"could not switch to context: {r.get('stderr') or r.get('message')}"}
    return {"ok": True, "kubeconfig": str(p), "context": context}


def k8s_clear_kubeconfig() -> Dict[str, Any]:
    from agcl.setup import _patch_env_file
    _patch_env_file({"KUBECONFIG": ""})
    os.environ.pop("KUBECONFIG", None)
    return {"ok": True, "cleared": ["KUBECONFIG"]}


# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------

def all_status() -> Dict[str, Any]:
    """One-shot dashboard read used by the GUI Integrations card."""
    return {
        "providers": check_all_providers()["providers"],
        "docker": docker_status(),
        "gcp": gcp_status(),
        "kubernetes": k8s_status(),
    }
