"""
agcl.setup_endpoints — HTTP routes for the setup wizards and downloaders.

Mounted under /node/setup/* by build_node_app(). Same auth gate as the
rest of /node/*.

Endpoints:

  GET  /node/setup/registry              recommended-models index
  GET  /node/setup/local-models          scan disk for cached HF + GGUF

  POST /node/setup/autoconfig            kick off canonical MAS setup (job)
  POST /node/setup/chat-quickstart       patch .env with chat defaults (sync)
  POST /node/setup/download/hf           snapshot-download HF repo (job)
  POST /node/setup/download/gguf         single GGUF file download (job)

  GET  /node/setup/jobs                  list recent jobs
  GET  /node/setup/jobs/{id}             job status snapshot
  GET  /node/setup/jobs/{id}/stream      SSE stream of job events
  POST /node/setup/jobs/{id}/cancel      request cancel

The download / autoconfig endpoints return immediately with a job_id;
clients poll /jobs/{id} or subscribe to /jobs/{id}/stream for progress.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agcl import setup as S


# ---- request bodies ----

class AutoconfigRequest(BaseModel):
    force: bool = False
    install_deps: bool = False
    download: bool = False


class ChatQuickstartRequest(BaseModel):
    local_model_path: Optional[str] = None
    default_cloud: Optional[str] = None       # "claude" | "openai"
    prefix_word_count: Optional[int] = None
    prompt_format: Optional[str] = None       # "chatml" | "plain"


class HFDownloadRequest(BaseModel):
    repo_id: str
    revision: Optional[str] = None
    dest: Optional[str] = None


class GGUFDownloadRequest(BaseModel):
    source: str                                # URL, "owner/repo", or "owner/repo:filename"
    filename: Optional[str] = None
    dest: Optional[str] = None


class ProviderCheckRequest(BaseModel):
    provider: Optional[str] = None             # "openai" | "anthropic" | "deepseek" | None=all
    key: Optional[str] = None                  # one-shot check without saving


class DockerLoginRequest(BaseModel):
    registry: str
    username: str
    password: str
    persist_reference: bool = True


class DockerLogoutRequest(BaseModel):
    registry: str


class GCPAuthRequest(BaseModel):
    service_account_path: str
    project: Optional[str] = None


class K8sAuthRequest(BaseModel):
    kubeconfig_path: str
    context: Optional[str] = None


class WizardAnswer(BaseModel):
    value: Optional[Any] = None


# ---- router ----

def make_setup_router() -> APIRouter:
    r = APIRouter(prefix="/node/setup", tags=["setup"])

    # ------------- registry / inventory -----------------

    @r.get("/registry")
    def get_registry():
        return S.model_registry()

    @r.get("/local-models")
    def get_local_models():
        return S.list_local_models()

    @r.post("/secrets/reload")
    def reload_secrets():
        """Re-read .env from disk so post-startup edits take effect
        without restarting the node. The /info and /config endpoints
        already read os.environ live, but this lets callers force a
        cold re-read of the .env file."""
        from agcl import secrets as sec
        loaded = sec.reload()
        # Mask values for the response so we never echo secrets back.
        masked = {
            k: f"***({len(v)} chars)" if any(t in k for t in ("KEY", "SECRET", "TOKEN", "PASSWORD"))
               else v
            for k, v in loaded.items()
        }
        return {
            "ok": True,
            "loaded": masked,
            "openai_key_present": bool(os.environ.get("OPENAI_API_KEY")),
            "claude_key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
        }

    # ------------- jobs ---------------------------------

    @r.get("/jobs")
    def jobs_list(limit: int = 50):
        return {"jobs": S.list_jobs(limit=limit)}

    @r.get("/jobs/{jid}")
    def job_status(jid: str):
        j = S.get_job(jid)
        if not j: raise HTTPException(404, "job not found")
        return j.snapshot()

    @r.get("/jobs/{jid}/stream")
    def job_stream(jid: str):
        j = S.get_job(jid)
        if not j: raise HTTPException(404, "job not found")

        async def gen():
            sent = 0
            # initial snapshot
            yield f"data: {json.dumps({'event': 'snapshot', **j.snapshot()})}\n\n"
            while True:
                evs = j.events_after(sent)
                for e in evs:
                    yield f"data: {json.dumps({'event': 'log', **e})}\n\n"
                sent += len(evs)
                if j.state in ("done", "error", "cancelled"):
                    yield f"data: {json.dumps({'event': 'final', **j.snapshot()})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                # wait for new events with a small timeout so the loop survives
                # client disconnects and adds a heartbeat every 15s.
                j._wake.clear()
                woke = await asyncio.get_event_loop().run_in_executor(
                    None, j._wake.wait, 15.0
                )
                if not woke:
                    yield f": keepalive {int(time.time())}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @r.post("/jobs/{jid}/cancel")
    def job_cancel(jid: str):
        if not S.cancel_job(jid):
            raise HTTPException(404, "job not found")
        return {"ok": True}

    # ------------- autoconfig ---------------------------

    @r.post("/autoconfig")
    def post_autoconfig(body: AutoconfigRequest):
        def runner(j):
            return S.auto_recursive(
                force=body.force,
                install_deps=body.install_deps,
                download=body.download,
                job=j,
            )
        j = S.start_job("autoconfig", runner)
        return {"ok": True, "job_id": j.id}

    # ------------- chat quick-setup (sync) ---------------

    @r.post("/chat-quickstart")
    def post_chat_quickstart(body: ChatQuickstartRequest):
        try:
            res = S.auto_chat(
                local_model_path=body.local_model_path,
                default_cloud=body.default_cloud,
                prefix_word_count=body.prefix_word_count,
                prompt_format=body.prompt_format,
            )
            return {"ok": True, **res}
        except ValueError as e:
            raise HTTPException(400, str(e))

    # ------------- HF + GGUF downloads ------------------

    @r.post("/download/hf")
    def post_download_hf(body: HFDownloadRequest):
        if not body.repo_id or "/" not in body.repo_id:
            raise HTTPException(400, "repo_id must look like 'owner/name'")
        def runner(j):
            return S.download_hf_snapshot(
                repo_id=body.repo_id,
                dest=body.dest,
                revision=body.revision,
                job=j,
            )
        j = S.start_job("download-hf", runner)
        return {"ok": True, "job_id": j.id, "repo_id": body.repo_id}

    @r.post("/download/gguf")
    def post_download_gguf(body: GGUFDownloadRequest):
        if not body.source:
            raise HTTPException(400, "source is required (URL or owner/repo[:filename])")
        def runner(j):
            return S.download_gguf(
                source=body.source,
                filename=body.filename,
                dest=body.dest,
                job=j,
            )
        j = S.start_job("download-gguf", runner)
        return {"ok": True, "job_id": j.id, "source": body.source}

    # ------------- providers: live check + quota -----------------------

    @r.get("/providers/status")
    def providers_status():
        from agcl import integrations_auth as IA
        return IA.check_all_providers()

    @r.post("/providers/check")
    def providers_check(body: ProviderCheckRequest):
        from agcl import integrations_auth as IA
        name = (body.provider or "").lower()
        if name == "openai":
            return {**IA.check_openai_key(body.key), "quota": IA.fetch_openai_quota(body.key)}
        if name in ("anthropic", "claude"):
            return {**IA.check_anthropic_key(body.key), "quota": IA.fetch_anthropic_quota(body.key)}
        if name == "deepseek":
            return {**IA.check_deepseek_key(body.key), "quota": IA.fetch_deepseek_balance(body.key)}
        if not name:
            return IA.check_all_providers()
        raise HTTPException(400, f"unknown provider: {name}")

    @r.get("/providers/{name}/quota")
    def providers_quota(name: str):
        from agcl import integrations_auth as IA
        name = name.lower()
        if name == "openai":   return IA.fetch_openai_quota()
        if name in ("anthropic", "claude"): return IA.fetch_anthropic_quota()
        if name == "deepseek": return IA.fetch_deepseek_balance()
        raise HTTPException(400, f"unknown provider: {name}")

    # ------------- ops-service auth: Docker / GCP / K8s ----------------

    @r.get("/auth/status")
    def auth_status_all():
        from agcl import integrations_auth as IA
        return IA.all_status()

    @r.get("/auth/docker")
    def auth_docker_status():
        from agcl import integrations_auth as IA
        return IA.docker_status()

    @r.post("/auth/docker/login")
    def auth_docker_login(body: DockerLoginRequest):
        from agcl import integrations_auth as IA
        res = IA.docker_login(body.registry, body.username, body.password,
                              persist_reference=body.persist_reference)
        if not res.get("ok"):
            raise HTTPException(400, res.get("message") or "login failed")
        return res

    @r.post("/auth/docker/logout")
    def auth_docker_logout(body: DockerLogoutRequest):
        from agcl import integrations_auth as IA
        return IA.docker_logout(body.registry)

    @r.get("/auth/gcp")
    def auth_gcp_status():
        from agcl import integrations_auth as IA
        return IA.gcp_status()

    @r.post("/auth/gcp/set")
    def auth_gcp_set(body: GCPAuthRequest):
        from agcl import integrations_auth as IA
        res = IA.gcp_set_credentials(body.service_account_path, body.project)
        if not res.get("ok"):
            raise HTTPException(400, res.get("message") or "gcp set failed")
        return res

    @r.post("/auth/gcp/clear")
    def auth_gcp_clear():
        from agcl import integrations_auth as IA
        return IA.gcp_clear_credentials()

    @r.get("/auth/k8s")
    def auth_k8s_status():
        from agcl import integrations_auth as IA
        return IA.k8s_status()

    @r.post("/auth/k8s/set")
    def auth_k8s_set(body: K8sAuthRequest):
        from agcl import integrations_auth as IA
        res = IA.k8s_set_kubeconfig(body.kubeconfig_path, body.context)
        if not res.get("ok"):
            raise HTTPException(400, res.get("message") or "k8s set failed")
        return res

    @r.post("/auth/k8s/clear")
    def auth_k8s_clear():
        from agcl import integrations_auth as IA
        return IA.k8s_clear_kubeconfig()

    # ------------- deploy wizards --------------------------------------

    @r.get("/wizards")
    def list_wizards_endpoint():
        from agcl import wizards as W
        return {"wizards": W.list_wizards()}

    @r.get("/wizards/{name}")
    def wizard_descriptor(name: str):
        from agcl import wizards as W
        desc = W.get_wizard(name)
        if not desc: raise HTTPException(404, f"unknown wizard: {name}")
        # Strip server-only fields that confuse the client
        return {"name": name, "title": desc["title"],
                "summary": desc.get("summary", ""),
                "category": desc.get("category", ""),
                "steps": desc["steps"]}

    @r.post("/wizards/{name}/start")
    def wizard_start(name: str):
        from agcl import wizards as W
        try:
            return W.start(name)
        except ValueError as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(400, str(e))

    @r.post("/wizards/sessions/{sid}/answer")
    def wizard_answer(sid: str, body: WizardAnswer):
        from agcl import wizards as W
        try:
            return W.answer(sid, body.value)
        except ValueError as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(400, str(e))

    @r.post("/wizards/sessions/{sid}/cancel")
    def wizard_cancel(sid: str):
        from agcl import wizards as W
        if not W.cancel_session(sid):
            raise HTTPException(404, "session not found")
        return {"ok": True}

    @r.get("/wizards/sessions/{sid}")
    def wizard_status(sid: str):
        from agcl import wizards as W
        sess = W.get_session(sid)
        if not sess: raise HTTPException(404, "session not found")
        return W._session_snapshot(sess)

    return r
