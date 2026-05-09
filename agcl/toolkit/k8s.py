"""
Kubernetes / Helm manifest emitter.

Generates a working Helm chart skeleton + standalone manifests for
deploying AGCL to any K8s cluster (kind/k3s, EKS/GKE/AKS, GPU clouds).

The chart layout matches docs/integrations/cloud/k8s.md.

Public surface:
    chart_files()        -> dict[str, str]  (Chart.yaml, values.yaml, templates/*)
    write_chart(out_dir) -> dict
    manifests()          -> dict[str, str]  (standalone deployment / service / pvc)
    ping()               -> dict             (kubectl available?)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict


# ----------------------------------------------------------------------
# Helm chart pieces
# ----------------------------------------------------------------------

CHART_YAML = """\
apiVersion: v2
name: agcl
description: AGCL — Agentic CLI runtime
type: application
version: 0.1.0
appVersion: "1.0.0"
"""

VALUES_YAML = """\
replicaCount: 1

image:
  repository: ghcr.io/yourorg/agcl
  tag: latest
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 9876

ingress:
  enabled: false
  className: ""
  hosts:
    - host: agcl.local
      paths:
        - path: /
          pathType: Prefix

agentState:
  persistence:
    enabled: true
    storageClass: ""
    size: 10Gi
    mountPath: /data/agent_state

redis:
  url: redis://agcl-redis:6379

llm:
  baseUrl: http://agcl-litellm:4000
  apiKey: sk-agcl-internal
  model: smart

s3:
  enabled: false
  bucket: ""
  endpoint: ""

resources:
  requests:
    cpu: "1"
    memory: "4Gi"
  limits:
    cpu: "4"
    memory: "16Gi"

gpu:
  enabled: false
  count: 1
  nodeSelector:
    nvidia.com/gpu.present: "true"
  tolerations:
    - key: nvidia.com/gpu
      operator: Exists
      effect: NoSchedule

env:
  - name: ANTHROPIC_API_KEY
    valueFrom: { secretKeyRef: { name: agcl-secrets, key: anthropic } }
  - name: OPENAI_API_KEY
    valueFrom: { secretKeyRef: { name: agcl-secrets, key: openai } }
"""


DEPLOYMENT_TPL = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "agcl.fullname" . }}
  labels:
    app.kubernetes.io/name: {{ include "agcl.name" . }}
    app.kubernetes.io/instance: {{ .Release.Name }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      app.kubernetes.io/name: {{ include "agcl.name" . }}
      app.kubernetes.io/instance: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: {{ include "agcl.name" . }}
        app.kubernetes.io/instance: {{ .Release.Name }}
    spec:
      {{- if .Values.gpu.enabled }}
      nodeSelector:
        {{- toYaml .Values.gpu.nodeSelector | nindent 8 }}
      tolerations:
        {{- toYaml .Values.gpu.tolerations | nindent 8 }}
      {{- end }}
      containers:
        - name: agcl
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - containerPort: {{ .Values.service.port }}
              name: http
          env:
            - name: STATE_DIR
              value: {{ .Values.agentState.persistence.mountPath | quote }}
            - name: AGCL_REDIS_URL
              value: {{ .Values.redis.url | quote }}
            - name: AGCL_LLM_BASE_URL
              value: {{ .Values.llm.baseUrl | quote }}
            - name: AGCL_LLM_API_KEY
              value: {{ .Values.llm.apiKey | quote }}
            - name: AGCL_LLM_MODEL
              value: {{ .Values.llm.model | quote }}
            {{- if .Values.s3.enabled }}
            - name: AGCL_S3_BUCKET
              value: {{ .Values.s3.bucket | quote }}
            {{- if .Values.s3.endpoint }}
            - name: AGCL_S3_ENDPOINT
              value: {{ .Values.s3.endpoint | quote }}
            {{- end }}
            {{- end }}
            {{- with .Values.env }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          livenessProbe:
            httpGet:
              path: /node/health
              port: http
            initialDelaySeconds: 30
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /node/health
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            {{- if .Values.gpu.enabled }}
            limits:
              nvidia.com/gpu: {{ .Values.gpu.count | quote }}
            {{- end }}
            {{- toYaml .Values.resources | nindent 12 }}
          volumeMounts:
            - name: state
              mountPath: {{ .Values.agentState.persistence.mountPath }}
      volumes:
        - name: state
          {{- if .Values.agentState.persistence.enabled }}
          persistentVolumeClaim:
            claimName: {{ include "agcl.fullname" . }}-state
          {{- else }}
          emptyDir: {}
          {{- end }}
"""

SERVICE_TPL = """\
apiVersion: v1
kind: Service
metadata:
  name: {{ include "agcl.fullname" . }}
  labels:
    app.kubernetes.io/name: {{ include "agcl.name" . }}
spec:
  type: {{ .Values.service.type }}
  ports:
    - port: {{ .Values.service.port }}
      targetPort: http
      protocol: TCP
      name: http
  selector:
    app.kubernetes.io/name: {{ include "agcl.name" . }}
    app.kubernetes.io/instance: {{ .Release.Name }}
"""

PVC_TPL = """\
{{- if .Values.agentState.persistence.enabled }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "agcl.fullname" . }}-state
spec:
  accessModes: [ReadWriteOnce]
  {{- if .Values.agentState.persistence.storageClass }}
  storageClassName: {{ .Values.agentState.persistence.storageClass }}
  {{- end }}
  resources:
    requests:
      storage: {{ .Values.agentState.persistence.size }}
{{- end }}
"""

HELPERS_TPL = """\
{{/* Common name + fullname helpers */}}
{{- define "agcl.name" -}}
{{- default "agcl" .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agcl.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default "agcl" .Values.nameOverride -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
"""


def chart_files() -> Dict[str, str]:
    return {
        "Chart.yaml":                CHART_YAML,
        "values.yaml":               VALUES_YAML,
        "templates/_helpers.tpl":    HELPERS_TPL,
        "templates/deployment.yaml": DEPLOYMENT_TPL,
        "templates/service.yaml":    SERVICE_TPL,
        "templates/pvc.yaml":        PVC_TPL,
    }


def write_chart(out_dir: str = "deploy/agcl-chart") -> Dict[str, Any]:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "templates").mkdir(parents=True, exist_ok=True)
    written = []
    for rel, body in chart_files().items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        written.append(str(p))
    return {"ok": True, "dir": str(root), "files": written}


# ----------------------------------------------------------------------
# Standalone manifests (no Helm needed)
# ----------------------------------------------------------------------

STANDALONE_DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agcl
  labels: { app: agcl }
spec:
  replicas: 1
  selector: { matchLabels: { app: agcl } }
  template:
    metadata: { labels: { app: agcl } }
    spec:
      containers:
        - name: agcl
          image: ghcr.io/yourorg/agcl:latest
          ports: [{ containerPort: 9876, name: http }]
          env:
            - { name: STATE_DIR,        value: "/data/agent_state" }
            - { name: AGCL_REDIS_URL,   value: "redis://agcl-redis:6379" }
            - { name: AGCL_LLM_BASE_URL, value: "http://agcl-litellm:4000" }
          livenessProbe:
            httpGet: { path: /node/health, port: http }
            initialDelaySeconds: 30
          readinessProbe:
            httpGet: { path: /node/health, port: http }
            initialDelaySeconds: 5
          resources:
            requests: { cpu: "1", memory: "4Gi" }
            limits:   { cpu: "4", memory: "16Gi" }
          volumeMounts:
            - { name: state, mountPath: /data/agent_state }
      volumes:
        - name: state
          persistentVolumeClaim: { claimName: agcl-state }
"""

STANDALONE_SERVICE = """\
apiVersion: v1
kind: Service
metadata:
  name: agcl
spec:
  type: ClusterIP
  ports: [{ port: 9876, targetPort: http, name: http }]
  selector: { app: agcl }
"""

STANDALONE_PVC = """\
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: agcl-state
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests: { storage: 10Gi }
"""


def manifests() -> Dict[str, str]:
    return {
        "deployment.yaml": STANDALONE_DEPLOYMENT,
        "service.yaml":    STANDALONE_SERVICE,
        "pvc.yaml":        STANDALONE_PVC,
    }


def write_manifests(out_dir: str = "deploy/k8s") -> Dict[str, Any]:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in manifests().items():
        p = root / name
        p.write_text(body)
        written.append(str(p))
    return {"ok": True, "dir": str(root), "files": written}


# ----------------------------------------------------------------------
# Live introspection
# ----------------------------------------------------------------------

def ping() -> Dict[str, Any]:
    """Is `kubectl` available + can it reach a cluster?"""
    kubectl = shutil.which("kubectl")
    if not kubectl:
        return {"ok": False, "available": False,
                "hint": "install kubectl: https://kubernetes.io/docs/tasks/tools/"}
    try:
        out = subprocess.check_output(
            [kubectl, "config", "current-context"], stderr=subprocess.STDOUT, timeout=3,
        ).decode().strip()
        return {"ok": True, "available": True, "context": out}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "available": True, "path": kubectl,
                "error": e.output.decode() if hasattr(e, "output") else str(e)}
