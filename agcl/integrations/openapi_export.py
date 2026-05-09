"""
OpenAPI 3.0 spec exporter for AGCL.

FastAPI builds a complete OpenAPI document for the node server
automatically; this helper just dumps it to stdout (or a file) so
platforms that consume static OpenAPI specs - Zapier, Copilot Studio,
n8n in webhook mode, Vertex AI Extensions - can register AGCL without
running the server.

Usage:
    python main.py openapi                       # stdout JSON
    python main.py openapi --out agcl.openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional


def export(out: Optional[str] = None) -> int:
    # Build the node app at the same configuration `python main.py
    # node` would, then ask FastAPI for its OpenAPI document. We pass
    # a placeholder auth key because the auth middleware needs one;
    # the spec doesn't bake the key in.
    from agcl.node import build_node_app
    app = build_node_app(auth_key="OPENAPI_DUMP", cors_origins=["*"])
    spec = app.openapi()
    blob = json.dumps(spec, indent=2)
    if out:
        Path(out).write_text(blob)
        print(f"  wrote {out}  ({len(blob)} bytes, {len(spec.get('paths', {}))} paths)")
    else:
        sys.stdout.write(blob + "\n")
    return 0
