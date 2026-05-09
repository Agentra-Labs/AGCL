"""
GCP Cloud Run deployment emitter.

Cloud Run is the recommended GCP host for stateless MCP-style servers
and the AGCL node — it scales to zero, supports always-on min instances
when you need cold-start avoidance, and gives you a public HTTPS URL
out of the box.

What this module produces:
    - service.yaml          Cloud Run v1 Service manifest (deploy via gcloud)
    - cloudbuild.yaml       Cloud Build pipeline (build image -> push -> deploy)
    - .gcloudignore         what to skip when uploading source
    - secret-manager.sh     helper script to create the API-key secrets

Auth model: Cloud Run sits behind IAM (`gcloud run services add-iam-policy-binding`).
For a public node, set --allow-unauthenticated and rely on AGCL's bearer auth.
For a private node, leave unauth off and grant `roles/run.invoker` to the caller.

Public surface:
    service_yaml(...)     -> str
    cloudbuild_yaml(...)  -> str
    write_to(out_dir)     -> dict
    deploy_command(...)   -> str   (gcloud one-liner; doesn't actually run it)
    ping()                -> dict  (`gcloud` available + which project)
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


def service_yaml(*,
                  name: str = "agcl-node",
                  region: str = "us-central1",
                  image: str = "gcr.io/PROJECT_ID/agcl:latest",
                  cpu: str = "2",
                  memory: str = "4Gi",
                  min_instances: int = 0,
                  max_instances: int = 5,
                  port: int = 9876,
                  allow_unauthenticated: bool = False,
                  env: Optional[Dict[str, str]] = None,
                  secrets: Optional[Dict[str, str]] = None,
                  ) -> str:
    """
    Build a Cloud Run v1 Service manifest. `secrets` maps
    {ENV_VAR: secret-manager-name}; values resolve at runtime.
    """
    env = env or {}
    secrets = secrets or {}

    env_lines: List[str] = []
    for k, v in env.items():
        env_lines.append(f"        - name: {k}\n          value: {v!r}")
    for k, sec in secrets.items():
        env_lines.append(
            f"        - name: {k}\n"
            f"          valueFrom:\n"
            f"            secretKeyRef:\n"
            f"              name: {sec}\n"
            f"              key: latest"
        )
    env_block = "\n".join(env_lines) if env_lines else "        []"

    return f"""\
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: {name}
  labels:
    cloud.googleapis.com/location: {region}
  annotations:
    run.googleapis.com/ingress: {"all" if allow_unauthenticated else "internal-and-cloud-load-balancing"}
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "{min_instances}"
        autoscaling.knative.dev/maxScale: "{max_instances}"
        run.googleapis.com/cpu-throttling: "false"
        run.googleapis.com/startup-cpu-boost: "true"
    spec:
      containerConcurrency: 80
      timeoutSeconds: 3600
      containers:
        - image: {image}
          ports:
            - name: http1
              containerPort: {port}
          resources:
            limits:
              cpu: {cpu!r}
              memory: {memory}
          env:
{env_block}
          startupProbe:
            httpGet:
              path: /node/health
              port: {port}
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 12
"""


def cloudbuild_yaml(*,
                     image: str = "gcr.io/$PROJECT_ID/agcl:$SHORT_SHA",
                     region: str = "us-central1",
                     service: str = "agcl-node",
                     ) -> str:
    return f"""\
steps:
  - name: gcr.io/cloud-builders/docker
    args: ['build', '-t', '{image}', '.']

  - name: gcr.io/cloud-builders/docker
    args: ['push', '{image}']

  - name: gcr.io/google.com/cloudsdktool/cloud-sdk
    entrypoint: gcloud
    args:
      - run
      - deploy
      - {service}
      - --image={image}
      - --region={region}
      - --platform=managed
      - --port=9876

images:
  - {image}

options:
  logging: CLOUD_LOGGING_ONLY
"""


def gcloudignore() -> str:
    return "\n".join([
        ".git/", ".gitignore",
        "__pycache__/", "*.py[cod]", "*.egg-info/",
        ".env", "*.env",
        ".agent_state/", ".claude/", "models/",
        "node_modules/", "clients/npm/dist/",
        "docs/", "*.md",
        "deploy/",
        "*.log",
    ]) + "\n"


def secret_manager_script(*,
                            project: str = "PROJECT_ID",
                            secrets: Optional[Dict[str, str]] = None,
                            ) -> str:
    """Emit a bash script that creates the named secrets via gcloud.
    Each secret value comes from the env so the script is safe to commit."""
    secrets = secrets or {
        "agcl-anthropic-key": "ANTHROPIC_API_KEY",
        "agcl-openai-key":    "OPENAI_API_KEY",
        "agcl-node-auth":     "AGCL_NODE_AUTH",
    }
    lines = [
        "#!/usr/bin/env bash",
        "# Create / update Secret Manager entries for AGCL on Cloud Run.",
        f"set -euo pipefail",
        f"PROJECT={project}",
        "",
        "gcloud config set project \"$PROJECT\"",
        "",
    ]
    for sec_name, env_name in secrets.items():
        lines += [
            f'if [[ -z "${{{env_name}:-}}" ]]; then',
            f'  echo "skip {sec_name}: ${env_name} not set in env"',
            f'else',
            f'  if gcloud secrets describe {sec_name} >/dev/null 2>&1; then',
            f'    printf "%s" "${{{env_name}}}" | gcloud secrets versions add {sec_name} --data-file=-',
            f'  else',
            f'    printf "%s" "${{{env_name}}}" | gcloud secrets create {sec_name} --data-file=-',
            f'  fi',
            f'fi',
            "",
        ]
    return "\n".join(lines)


def deploy_command(*,
                    project: str = "PROJECT_ID",
                    service: str = "agcl-node",
                    region: str = "us-central1",
                    image: Optional[str] = None,
                    allow_unauthenticated: bool = False,
                    ) -> str:
    """
    Return the one-liner the user runs after editing service.yaml.
    Doesn't execute anything — just prints the command to copy/paste.
    """
    img = image or f"gcr.io/{project}/agcl:latest"
    auth = "--allow-unauthenticated" if allow_unauthenticated else "--no-allow-unauthenticated"
    return (
        f"gcloud run deploy {service} "
        f"--image={img} --region={region} --platform=managed "
        f"--port=9876 {auth} --project={project}"
    )


def write_to(out_dir: str = "deploy/gcp",
              project: str = "PROJECT_ID",
              service: str = "agcl-node",
              region: str = "us-central1",
              ) -> Dict[str, Any]:
    """Materialize all GCP artifacts."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    files = {
        "service.yaml":    service_yaml(
            name=service, region=region,
            image=f"gcr.io/{project}/{service}:latest",
            secrets={
                "ANTHROPIC_API_KEY": "agcl-anthropic-key",
                "OPENAI_API_KEY":    "agcl-openai-key",
                "AGCL_NODE_AUTH":    "agcl-node-auth",
            },
            env={"STATE_DIR": "/tmp/agent_state"},
        ),
        "cloudbuild.yaml":  cloudbuild_yaml(
            image=f"gcr.io/{project}/{service}:$SHORT_SHA",
            region=region, service=service,
        ),
        ".gcloudignore":    gcloudignore(),
        "secret-manager.sh": secret_manager_script(project=project),
    }
    for name, body in files.items():
        p = out / name
        p.write_text(body)
        if name.endswith(".sh"):
            p.chmod(0o755)

    cmd = deploy_command(project=project, service=service, region=region)
    (out / "deploy.cmd.txt").write_text(cmd + "\n")

    return {"ok": True, "dir": str(out),
            "files": [str(out / f) for f in files] + [str(out / "deploy.cmd.txt")],
            "deploy_command": cmd}


def ping() -> Dict[str, Any]:
    """Is `gcloud` installed + which project is active?"""
    g = shutil.which("gcloud")
    if not g:
        return {"ok": False, "available": False,
                "hint": "install gcloud SDK: https://cloud.google.com/sdk/docs/install"}
    try:
        out = subprocess.check_output(
            [g, "config", "get-value", "project"],
            stderr=subprocess.STDOUT, timeout=5,
        ).decode().strip()
        return {"ok": True, "available": True, "project": out, "path": g}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "available": True, "path": g, "error": str(e)}
