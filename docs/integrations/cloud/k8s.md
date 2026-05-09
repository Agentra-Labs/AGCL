# Kubernetes — infra packaging

<p align="left">
  <img src="../../assets/kubernetes.svg" width="22" alt="Kubernetes" />&nbsp;
  <a href="https://img.shields.io/badge/Helm-3.x-0F1689"><img alt="Helm" src="https://img.shields.io/badge/Helm-3.x-0F1689"></a>
  <img src="../../assets/nvidia.svg" width="22" alt="NVIDIA" />&nbsp;
  <img src="../../assets/docker.svg" width="22" alt="Docker" />
</p>

**What it does:** Makes AGCL deployable on any cluster — local
(kind / k3s), cloud (EKS / GKE / AKS), or GPU cloud (RunPod K8s,
Lambda, Vast.ai).

> Part of the **[Cloud / infra integrations](../cloud.md)**.
> If you only want a single-host stack, see
> **[../docker.md](../docker.md)** instead.

---

## Helm chart structure

```
agcl-chart/
├── Chart.yaml
├── values.yaml
└── templates/
    ├── deployment.yaml
    ├── service.yaml
    ├── persistentvolumeclaim.yaml
    ├── configmap.yaml
    └── secret.yaml
```

**`values.yaml`:**

```yaml
replicaCount: 1

image:
  repository: ghcr.io/yourorg/agcl
  tag: latest

agentState:
  persistence:
    enabled: true
    storageClass: gp3
    size: 10Gi
    mountPath: /data/agent_state

redis:
  url: redis://agcl-redis:6379

llm:
  baseUrl: http://agcl-litellm:4000
  apiKey: sk-agcl-internal

gpu:
  enabled: true
  count: 1
```

---

## GPU scheduling (`deployment.yaml` snippet)

```yaml
spec:
  containers:
    - name: agcl
      resources:
        limits:
          nvidia.com/gpu: "1"     # request GPU as first-class resource
        requests:
          memory: 8Gi
          cpu: "2"
  nodeSelector:
    nvidia.com/gpu.present: "true"
  tolerations:
    - key: nvidia.com/gpu
      operator: Exists
      effect: NoSchedule
```

---

## PVC for `.agent_state`

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: agcl-state-pvc
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: gp3
  resources:
    requests:
      storage: 10Gi
```

---

## NVIDIA GPU Operator

Install once per cluster:

```bash
helm repo add nvidia https://nvidia.github.io/gpu-operator
helm upgrade --install gpu-operator nvidia/gpu-operator \
  -n gpu-operator --create-namespace \
  --set driver.enabled=true \
  --set toolkit.enabled=true \
  --set mig.strategy=single
```

This installs drivers, container toolkit, DCGM metrics exporter, and
device plugin — GPUs then appear as `nvidia.com/gpu` resources
schedulable by pods.

---

## GPU cloud K8s targets

| Platform | K8s support |
|---|---|
| **RunPod** | Kubernetes pods via RunPod API; custom Docker templates; GPU allocation per pod |
| **Vast.ai** | Docker-based; use `--open-port` + Docker Compose; no native K8s but Docker Swarm works |
| **Lambda Labs** | Bare-metal instances → install k3s or kubeadm, use NVIDIA GPU Operator |
| **Paperspace** | Gradient offers managed K8s clusters with GPU node pools |
| **local (k3s)** | `curl -sfL https://get.k3s.io \| sh -` → lightweight single-node K8s |
