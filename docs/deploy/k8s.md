# Deploy AGCL to Kubernetes

Run AGCL on any Kubernetes cluster — local (kind / k3s / minikube),
managed (GKE / EKS / AKS), or self-hosted. AGCL ships both raw manifests
and a Helm chart skeleton.

> Source / emitter: [agcl/toolkit/k8s.py](../../agcl/toolkit/k8s.py)
> · auth helper: [agcl/integrations_auth.py](../../agcl/integrations_auth.py)
> · reference: [docs/integrations/cloud/k8s.md](../integrations/cloud/k8s.md)

---

## What you're building

```
┌─────────────────── Kubernetes namespace: agcl ─────────────────────┐
│   ┌──────────────────┐    ┌──────────────────┐    ┌──────────────┐ │
│   │  Deployment      │    │  Service         │    │  Ingress     │ │
│   │  agcl-node       │◄───┤  ClusterIP:9876  │◄───┤  (optional)  │ │
│   │  (1-N replicas)  │    │                  │    │  TLS cert    │ │
│   └─────────┬────────┘    └──────────────────┘    └──────────────┘ │
│             │                                                       │
│   ┌─────────▼──────────┐  ┌──────────────────┐  ┌──────────────┐   │
│   │ PVC: agcl-state    │  │ Secret: agcl-env │  │ (optional)   │   │
│   │  /app/.agent_state │  │  AGCL_NODE_AUTH  │  │ Redis        │   │
│   │  /app/models       │  │  OPENAI_API_KEY  │  │ vLLM / Ollama│   │
│   └────────────────────┘  └──────────────────┘  └──────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

| Need                                          | How to check                          |
|-----------------------------------------------|---------------------------------------|
| `kubectl` configured + reachable cluster      | `kubectl cluster-info`                |
| (Optional) `helm` ≥ 3                         | `helm version`                        |
| A container image of AGCL pushed somewhere    | See [docker.md](docker.md#pushing-the-image-to-a-private-registry) |
| Cluster has enough disk for `models/` (≥10 GB)| `kubectl describe nodes`              |

---

## Quick path — emit the manifests

### From the web console

1. **Deploy** tab → tick the options (GPU, Redis, etc.).
2. Click **Emit Kubernetes**.
3. **Download .json** — the response contains every YAML file ready to
   `kubectl apply -f -`.

### From the CLI

```bash
python main.py toolkit emit-k8s
```

Writes to `deploy/agcl-chart/` (Helm) and `deploy/k8s/` (standalone
manifests).

---

## Manual path — every step

### 1. Connect kubectl

From the web console: Setup → Integrations → Kubernetes → paste the
path to your kubeconfig (e.g. `/home/you/.kube/config`) → **Connect**.
AGCL writes `KUBECONFIG` to `.env` and switches contexts if you
provided one.

From the CLI:

```bash
python main.py auth k8s-set ~/.kube/config --context my-cluster
python main.py auth k8s-status
# → {"ok": true, "cluster_reachable": true, "namespaces": [...], ...}
```

### 2. Create the namespace

```bash
kubectl create namespace agcl
kubectl config set-context --current --namespace=agcl
```

### 3. Create the secret

The Deployment reads bearer key + API keys from a Kubernetes Secret.
Do this from your `.env` (don't paste secrets into manifests):

```bash
kubectl create secret generic agcl-env \
    --from-env-file=.env
```

Verify:

```bash
kubectl get secret agcl-env -o jsonpath='{.data}' | jq 'keys'
# → ["ANTHROPIC_API_KEY", "AGCL_NODE_AUTH", ...]
```

### 4. Create the PVC (if you want persistence)

```bash
kubectl apply -f deploy/k8s/pvc.yaml
```

The emitted PVC asks for 20 GiB by default — adjust if you'll cache
big HF models.

### 5. Apply the Deployment + Service

```bash
kubectl apply -f deploy/k8s/deployment.yaml
kubectl apply -f deploy/k8s/service.yaml
```

The Deployment uses `envFrom.secretRef: agcl-env` so every key from
the secret lands as an env var in the container.

### 6. Expose it

**Cluster-internal** (default): the Service is `ClusterIP`. Use
`kubectl port-forward` for ad-hoc access:

```bash
kubectl port-forward svc/agcl-node 9876:9876
# now http://localhost:9876 works locally
```

**Public via LoadBalancer**:

```bash
kubectl patch svc agcl-node -p '{"spec":{"type":"LoadBalancer"}}'
kubectl get svc agcl-node -w   # wait for EXTERNAL-IP
```

**Public via Ingress** (recommended — TLS, single domain):

```bash
kubectl apply -f deploy/k8s/ingress.yaml
```

The emitted Ingress assumes you have an Ingress controller installed
(nginx-ingress or traefik) and cert-manager for TLS.

---

## Helm path

```bash
helm install agcl deploy/agcl-chart \
    --namespace agcl --create-namespace \
    --set image.repository=ghcr.io/your-user/agcl-node \
    --set image.tag=0.1.0 \
    --set-file envSecret=.env
```

Useful overrides in `values.yaml`:

| Key                          | What it does                                |
|------------------------------|---------------------------------------------|
| `replicaCount`               | Pod replicas. Stay at 1 unless using Redis. |
| `image.repository / tag`     | Where the image lives                       |
| `resources.{requests,limits}`| CPU + memory caps                           |
| `gpu.enabled`                | Add nvidia.com/gpu request                  |
| `persistence.size`           | PVC size                                    |
| `ingress.enabled / host / tls`| Ingress wiring                             |

Upgrade after changing config:

```bash
helm upgrade agcl deploy/agcl-chart -n agcl --reuse-values \
    --set image.tag=0.2.0
```

---

## Multi-replica setup (with Redis)

The default config holds sessions in memory + on disk per-pod. If you
run more than one replica, sessions on pod A aren't visible to pod B.

Fix: deploy a Redis (or Valkey) instance and point AGCL at it.

```bash
# 1. Install Redis via Helm
helm install agcl-redis bitnami/redis \
    --namespace agcl \
    --set auth.enabled=false   # or set up a password and put it in the secret

# 2. Patch the AGCL secret with AGCL_REDIS_URL
kubectl create secret generic agcl-env \
    --from-env-file=.env \
    --from-literal=AGCL_REDIS_URL=redis://agcl-redis-master:6379 \
    --dry-run=client -o yaml | kubectl replace -f -

# 3. Roll the Deployment to pick up the new secret
kubectl rollout restart deploy/agcl-node
```

See [redis_state.md](redis_state.md) for the rest.

---

## Verify it works

```bash
# Liveness via port-forward
kubectl port-forward svc/agcl-node 9876:9876 &
curl http://localhost:9876/node/health
kill %1

# Or, if you expose via Ingress / LoadBalancer:
curl https://agcl.example.com/node/health

# Auth-gated probe
KEY=$(kubectl get secret agcl-env -o jsonpath='{.data.AGCL_NODE_AUTH}' | base64 -d)
curl -X POST http://localhost:9876/node/auth/verify \
     -H "Authorization: Bearer $KEY"
```

Probe a remote AGCL node from another node using the **CLI Actions →
Verify a remote node URL** form in the GUI.

---

## Common problems

| Symptom                                       | Fix                                                                                                      |
|-----------------------------------------------|----------------------------------------------------------------------------------------------------------|
| Pod `CrashLoopBackOff`                        | `kubectl logs deploy/agcl-node` — usually a missing key in the secret.                                    |
| `ImagePullBackOff`                            | Image isn't in a registry the cluster can pull. Push it; if private, add `imagePullSecrets`.              |
| Sessions disappear between requests           | Two pods, no shared state. Either pin `replicas: 1` or add Redis (above).                                  |
| OOM kills                                     | Bump `resources.limits.memory`. GGUF models are memory-mapped; HF models load into RAM. Plan for ~2× model size. |
| GPU not scheduled                             | Cluster has no GPU nodes, or the device plugin isn't installed. `kubectl get nodes -o yaml | grep nvidia.com`. |
| Ingress 502s                                  | Pod is up but probe failed. Check `readinessProbe` matches `/node/health`.                                 |
| Bearer-key rotation                           | Update the secret + `kubectl rollout restart deploy/agcl-node`.                                            |

---

## What to read next

- [gcp.md](gcp.md) — same concepts, fully managed (Cloud Run).
- [redis_state.md](redis_state.md) — required if you scale replicas.
- [s3_store.md](s3_store.md) — required if you scale and persist topic checkpoints.
- [docs/security.md](../security.md) — hardening before going public.
