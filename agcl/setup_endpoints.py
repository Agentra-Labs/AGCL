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

    return r
