# Deploy AGCL to Google Cloud Run

Cloud Run gives you a public HTTPS URL, scale-to-zero (no idle cost),
and managed TLS for an AGCL node — useful if you don't want to run a
VM or a Kubernetes cluster.

> Source / emitter: [agcl/toolkit/gcp.py](../../agcl/toolkit/gcp.py)
> · auth helper: [agcl/integrations_auth.py](../../agcl/integrations_auth.py)
> · reference: [docs/integrations/cloud/gcp.md](../integrations/cloud/gcp.md)

---

## What you're building

```
   Internet
      │  HTTPS
┌─────▼────────────────────────────────────────────────┐
│  Google Cloud Run service: agcl-node                 │
│   ─ scale 0..N                                       │
│   ─ env from Secret Manager (API keys, bearer)        │
│   ─ region of your choice                             │
│   ─ allow_unauthenticated (you rely on AGCL bearer)   │
└──────────────────┬───────────────────────────────────┘
                   │
                   ▼
        ┌────────────────────┐
        │  Artifact Registry │  ← container image
        │  Cloud Build        │  ← (optional) builds from git
        └────────────────────┘
```

## Prerequisites

| Need                                         | How                                  |
|----------------------------------------------|---------------------------------------|
| A Google Cloud project                       | https://console.cloud.google.com/     |
| `gcloud` CLI installed and authenticated     | `gcloud auth login`                   |
| Billing enabled on the project               | console → Billing                      |
| APIs enabled                                 | see step 1                            |
| A service-account JSON key                   | step 2                                |

Approx **free tier** covers a small dev node (2M req/month is free).
Heavy use will incur Cloud Run + Artifact Registry + Secret Manager
costs.

---

## Quick path — emit + deploy

```bash
# 1. Generate manifests
python main.py toolkit emit-gcp

# 2. Read the deploy command back (printed in the emitter output)
cat deploy/gcp/service.yaml         # the Cloud Run Service manifest
cat deploy/gcp/cloudbuild.yaml      # (optional) build+push+deploy pipeline
bash deploy/gcp/secret-manager.sh   # creates the secret resources
```

The `deploy_command` field in the emitter response is the single
`gcloud run deploy …` invocation that wires everything together.

---

## Manual path — every step

### 1. Enable the APIs

```bash
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    secretmanager.googleapis.com
```

### 2. Create a service account + key

A dedicated service account is best practice — don't use your personal
account credentials for production deploys.

```bash
gcloud iam service-accounts create agcl-deployer \
    --display-name="AGCL deployer"

# Roles needed to push an image and deploy Cloud Run
PROJECT_ID=$(gcloud config get-value project)
for role in roles/run.admin roles/artifactregistry.writer roles/cloudbuild.builds.editor roles/secretmanager.admin roles/iam.serviceAccountUser; do
    gcloud projects add-iam-policy-binding $PROJECT_ID \
        --member="serviceAccount:agcl-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
        --role="$role"
done

# Download the JSON key
gcloud iam service-accounts keys create ~/agcl-gcp.json \
    --iam-account="agcl-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
```

### 3. Tell AGCL about the credentials

**GUI**: Setup → Integrations → Google Cloud → paste the path
(`~/agcl-gcp.json`) + project id → **Connect**. AGCL parses the JSON,
saves `GOOGLE_APPLICATION_CREDENTIALS` and `GOOGLE_CLOUD_PROJECT` to
`.env`, and shows the service-account email.

**CLI**:

```bash
python main.py auth gcp-set ~/agcl-gcp.json --project $PROJECT_ID
python main.py auth gcp-status
# → {"ok": true, "service_account": {"client_email": "...", ...}, ...}
```

### 4. Create an Artifact Registry repo

```bash
gcloud artifacts repositories create agcl \
    --repository-format=docker \
    --location=us-central1
```

### 5. Build + push the image

Option A — Cloud Build (no local Docker needed):

```bash
gcloud builds submit \
    --tag us-central1-docker.pkg.dev/$PROJECT_ID/agcl/agcl-node:0.1.0
```

Option B — local Docker, push to GAR:

```bash
gcloud auth configure-docker us-central1-docker.pkg.dev
docker build -t us-central1-docker.pkg.dev/$PROJECT_ID/agcl/agcl-node:0.1.0 .
docker push us-central1-docker.pkg.dev/$PROJECT_ID/agcl/agcl-node:0.1.0
```

### 6. Stash secrets in Secret Manager

You don't want API keys baked into the container image. Use Secret
Manager and mount them as env vars.

```bash
# Bearer key (pinned so the GUI doesn't have to re-paste on every revision)
echo -n $(openssl rand -hex 32) | gcloud secrets create AGCL_NODE_AUTH --data-file=-

# API keys
echo -n "sk-ant-…" | gcloud secrets create ANTHROPIC_API_KEY --data-file=-
echo -n "sk-…"     | gcloud secrets create OPENAI_API_KEY    --data-file=-

# Give Cloud Run service-agent access
RUNTIME_SA="${PROJECT_ID}-compute@developer.gserviceaccount.com"
for s in AGCL_NODE_AUTH ANTHROPIC_API_KEY OPENAI_API_KEY; do
    gcloud secrets add-iam-policy-binding $s \
        --member="serviceAccount:${RUNTIME_SA}" \
        --role="roles/secretmanager.secretAccessor"
done
```

### 7. Deploy to Cloud Run

```bash
gcloud run deploy agcl-node \
    --image us-central1-docker.pkg.dev/$PROJECT_ID/agcl/agcl-node:0.1.0 \
    --platform managed \
    --region us-central1 \
    --port 9876 \
    --allow-unauthenticated \
    --cpu 2 --memory 2Gi \
    --min-instances 0 \
    --max-instances 3 \
    --set-env-vars="DEFAULT_CLOUD=claude" \
    --set-secrets="AGCL_NODE_AUTH=AGCL_NODE_AUTH:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest" \
    --command="python" \
    --args="main.py,node,--bind,0.0.0.0,--port,9876,--cors,*"
```

The command will print the service URL, e.g.
`https://agcl-node-abc123-uc.a.run.app`. That's your public HTTPS
endpoint.

### 8. Connect from the GUI

In the web console's login pane:

- Host: `https://agcl-node-abc123-uc.a.run.app`
- Bearer key: `gcloud secrets versions access latest --secret=AGCL_NODE_AUTH`

---

## CORS for a hosted GUI

If you serve the web console from GitHub Pages, restrict CORS to your
Pages domain:

```bash
gcloud run services update agcl-node \
    --update-args="main.py,node,--bind,0.0.0.0,--port,9876,--cors,https://your-user.github.io"
```

---

## Verify it works

```bash
SERVICE_URL=$(gcloud run services describe agcl-node \
    --region us-central1 --format='value(status.url)')

# Public liveness (no auth)
curl $SERVICE_URL/node/health

# Authed call
KEY=$(gcloud secrets versions access latest --secret=AGCL_NODE_AUTH)
curl $SERVICE_URL/node/info -H "Authorization: Bearer $KEY" | jq '.platform.providers'
# → {"openai": true, "claude": true}
```

---

## Cost tips

- **Min instances = 0** unless cold-starts hurt. With min = 0, you pay
  only when a request is in flight.
- **CPU allocated only during requests** (default). Otherwise you pay
  for idle CPU. Don't change this unless you need background work.
- **Region matters** for egress cost. Pick the region closest to your
  callers.
- **Cloud Run Free Tier** covers 2M req/month, 360k GB-sec memory,
  180k vCPU-sec — plenty for a personal node.

---

## Common problems

| Symptom                                          | Fix                                                                                                |
|--------------------------------------------------|----------------------------------------------------------------------------------------------------|
| `gcloud run deploy` → "permission denied on artifactregistry" | Add `roles/artifactregistry.writer` to your deployer SA.                                            |
| Service URL returns 503 on first request         | Cold start. The first hit while scaled-to-zero takes a few seconds; subsequent hits are fast.       |
| Bearer key changes randomly                      | You used `--set-env-vars=AGCL_NODE_AUTH=…` instead of `--set-secrets`. Each deploy regenerates the env var unless it's pinned via Secret Manager. |
| `module not found: huggingface_hub` at runtime   | The image was built without dev extras. Rebuild with `pip install -e ".[dev]"`.                     |
| 32 MB streaming response cut off                  | Cloud Run's default request-response cap. For long SSE streams, set `--timeout=3600` on the deploy. |
| GUI mixed-content blocked                         | Cloud Run gives you HTTPS — point the GUI at `https://…` not `http://…`.                            |
| Egress to OpenAI / Anthropic timing out          | Cloud Run with a VPC connector needs an explicit egress rule. Default routing works for most users. |

---

## Cleaning up

```bash
gcloud run services delete agcl-node --region us-central1
gcloud artifacts repositories delete agcl --location us-central1
for s in AGCL_NODE_AUTH ANTHROPIC_API_KEY OPENAI_API_KEY; do
    gcloud secrets delete $s --quiet
done
```

---

## What to read next

- For private Cloud Run (IAM-gated, no public exposure):
  [docs/integrations/cloud/gcp.md](../integrations/cloud/gcp.md)
- For a non-managed alternative: [k8s.md](k8s.md) on GKE.
- Hardening checklist before going public: [docs/security.md](../security.md).
