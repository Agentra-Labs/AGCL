# GCP Cloud Run hosting

<p align="left">
  <a href="https://img.shields.io/badge/GCP-Cloud%20Run-4285F4?logo=googlecloud&logoColor=white"><img alt="Cloud Run" src="https://img.shields.io/badge/GCP-Cloud%20Run-4285F4?logo=googlecloud&logoColor=white"></a>
  <a href="https://img.shields.io/badge/Cloud%20Build-pipeline-4285F4"><img alt="Cloud Build" src="https://img.shields.io/badge/Cloud%20Build-pipeline-4285F4"></a>
  <a href="https://img.shields.io/badge/Secret%20Manager-keys-4285F4"><img alt="Secret Manager" src="https://img.shields.io/badge/Secret%20Manager-keys-4285F4"></a>
</p>

Cloud Run is the right GCP host for an AGCL node:

- Scales to zero when idle (the AGCL idle-watcher's "flush + unload" pairs nicely)
- Min-instances option to avoid cold starts when you want
- Public HTTPS URL with IAM (or `--allow-unauthenticated` + AGCL's own bearer auth)
- Secret Manager for `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `AGCL_NODE_AUTH`

> Part of the **[Cloud / infra integrations](../cloud.md)**.

---

## Generate the deploy artifacts

```bash
python main.py toolkit emit-gcp --out deploy/gcp
```

Or programmatically:

```python
from agcl.toolkit import gcp
gcp.write_to(out_dir="deploy/gcp", project="my-gcp-project",
              service="agcl-node", region="us-central1")
```

You get:

```
deploy/gcp/
├── service.yaml          Cloud Run v1 Service manifest
├── cloudbuild.yaml       Cloud Build pipeline (build → push → deploy)
├── .gcloudignore         what NOT to upload
├── secret-manager.sh     creates the named secrets from your local env
└── deploy.cmd.txt        the gcloud one-liner you'll run
```

`service.yaml` already wires three secrets via `secretKeyRef`:

| Env in container | Secret name |
|---|---|
| `ANTHROPIC_API_KEY` | `agcl-anthropic-key` |
| `OPENAI_API_KEY`    | `agcl-openai-key` |
| `AGCL_NODE_AUTH`    | `agcl-node-auth` |

---

## One-time setup

```bash
# 1. Create the secrets from your local env
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export AGCL_NODE_AUTH=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
bash deploy/gcp/secret-manager.sh

# 2. Build + push the image (Cloud Build does both)
gcloud builds submit --config=deploy/gcp/cloudbuild.yaml --project=my-gcp-project

# 3. Deploy
gcloud run services replace deploy/gcp/service.yaml \
  --region=us-central1 --project=my-gcp-project

# 4. (optional) Make it public
gcloud run services add-iam-policy-binding agcl-node \
  --region=us-central1 --member=allUsers --role=roles/run.invoker
```

---

## Auth model

Two clean options:

| Mode | When | How |
|---|---|---|
| **Public** + AGCL bearer | You want a single URL for any GUI on any network | `--allow-unauthenticated` on Cloud Run, AGCL's bearer auth still required (`Authorization: Bearer <AGCL_NODE_AUTH>`) |
| **Private** + Cloud Run IAM | Internal-only; Cloud Build / a colleague's gcloud CLI calls in | No `--allow-unauthenticated`. Grant `roles/run.invoker` to the service-account that calls. Caller signs every request via `gcloud auth print-identity-token`. |

`emit-gcp` defaults to private. Flip with `--allow-unauthenticated` on
the deploy command, or edit `service.yaml`'s
`run.googleapis.com/ingress` annotation.

---

## MCP hosting on Cloud Run

The same image hosts the MCP server when launched with a different
`CMD`. Either:

```yaml
# service.yaml
spec:
  template:
    spec:
      containers:
        - command: ["python", "main.py", "mcp", "--transport", "sse",
                     "--host", "0.0.0.0", "--port", "8765"]
```

…or deploy a second Cloud Run service named `agcl-mcp` reusing the
same image. The MCP-over-SSE URL becomes
`https://agcl-mcp-<hash>.run.app/sse`, which any HTTP MCP client
(Copilot Studio, OpenAI Agents SDK, Vercel AI SDK) can connect to.

---

## Why Cloud Run scales nicely with AGCL

- **Idle flush**: AGCL already flushes sessions to disk at
  `IDLE_FLUSH_SEC`. Cloud Run scales the instance away after that, and
  the next request reloads from `STATE_DIR`. Set `STATE_DIR` to a
  Cloud Storage FUSE mount or to S3-compatible storage (see
  [`s3.md`](s3.md)) for cross-instance persistence.
- **Min instances**: set `autoscaling.knative.dev/minScale: "1"` if you
  want the local llama.cpp prefix model warm at all times. (Default is
  zero — first request after idle pays the model-load cost.)
- **Concurrency**: `containerConcurrency: 80` is fine for the chat
  endpoint. The recursive-MAS `/mas/run` is heavier; consider running
  it on a dedicated service with concurrency `1`.
