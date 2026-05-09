"""
Object-storage-backed checkpoint store.

Stores trained-topic artifacts (links.pt, centroid.pt, meta.json) in
any S3-compatible backend: AWS S3, Cloudflare R2, MinIO, Backblaze B2.

Two implementations behind one surface:
    - LocalCheckpointStore   filesystem under <state_dir>/topics/
    - S3CheckpointStore      aioboto3 to any S3-compatible endpoint

`aioboto3` is an optional dependency. If it's missing, instantiating
S3CheckpointStore raises ImportError with a hint.

Surface:
    save_blob(topic_id, name, data: bytes) -> None
    load_blob(topic_id, name) -> bytes
    delete_topic(topic_id) -> bool
    list_topics() -> List[str]
    ping() -> dict
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


class LocalCheckpointStore:
    """Filesystem fallback. Lays out: <root>/<topic_id>/<name>"""

    def __init__(self, root: Optional[str] = None):
        from agcl import config as cfg
        self.root = Path(root or cfg.STATE_DIR) / "topics"
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def kind(self) -> str: return "local"

    def _topic_dir(self, topic_id: str) -> Path:
        return self.root / topic_id

    async def save_blob(self, topic_id: str, name: str, data: bytes) -> None:
        d = self._topic_dir(topic_id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, d / name)

    async def load_blob(self, topic_id: str, name: str) -> bytes:
        return (self._topic_dir(topic_id) / name).read_bytes()

    async def has_blob(self, topic_id: str, name: str) -> bool:
        return (self._topic_dir(topic_id) / name).exists()

    async def delete_topic(self, topic_id: str) -> bool:
        d = self._topic_dir(topic_id)
        if not d.exists():
            return False
        for f in d.iterdir():
            try: f.unlink()
            except FileNotFoundError: pass
        d.rmdir()
        return True

    async def list_topics(self) -> List[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    async def ping(self) -> Dict[str, Any]:
        return {"ok": True, "kind": "local", "root": str(self.root)}

    async def aclose(self) -> None: pass


class S3CheckpointStore:
    """
    aioboto3 client. Uses env:
        AGCL_S3_BUCKET (required)
        AGCL_S3_KEY_ID
        AGCL_S3_SECRET
        AGCL_S3_ENDPOINT  (blank = AWS; set for R2/MinIO/B2)
        AGCL_S3_REGION    (default: us-east-1)
        AGCL_S3_PREFIX    (key prefix; default: "topics/")
    """

    def __init__(self):
        try:
            import aioboto3  # type: ignore
        except ImportError as e:
            raise ImportError(
                "S3CheckpointStore requires `pip install aioboto3`"
            ) from e
        self.session = aioboto3.Session()
        self.bucket = os.environ["AGCL_S3_BUCKET"]
        self.prefix = os.getenv("AGCL_S3_PREFIX", "topics/").rstrip("/") + "/"
        self.endpoint = os.getenv("AGCL_S3_ENDPOINT") or None
        self.region = os.getenv("AGCL_S3_REGION", "us-east-1")
        self._kwargs = dict(
            endpoint_url=self.endpoint,
            aws_access_key_id=os.getenv("AGCL_S3_KEY_ID"),
            aws_secret_access_key=os.getenv("AGCL_S3_SECRET"),
            region_name=self.region,
        )

    @property
    def kind(self) -> str: return "s3"

    def _key(self, topic_id: str, name: str) -> str:
        return f"{self.prefix}{topic_id}/{name}"

    async def save_blob(self, topic_id: str, name: str, data: bytes) -> None:
        async with self.session.client("s3", **self._kwargs) as s3:
            await s3.put_object(
                Bucket=self.bucket, Key=self._key(topic_id, name), Body=data,
            )

    async def load_blob(self, topic_id: str, name: str) -> bytes:
        async with self.session.client("s3", **self._kwargs) as s3:
            obj = await s3.get_object(
                Bucket=self.bucket, Key=self._key(topic_id, name),
            )
            return await obj["Body"].read()

    async def has_blob(self, topic_id: str, name: str) -> bool:
        async with self.session.client("s3", **self._kwargs) as s3:
            try:
                await s3.head_object(
                    Bucket=self.bucket, Key=self._key(topic_id, name),
                )
                return True
            except Exception:
                return False

    async def delete_topic(self, topic_id: str) -> bool:
        deleted = False
        prefix = f"{self.prefix}{topic_id}/"
        async with self.session.client("s3", **self._kwargs) as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                contents = page.get("Contents") or []
                if not contents:
                    continue
                deleted = True
                await s3.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": o["Key"]} for o in contents]},
                )
        return deleted

    async def list_topics(self) -> List[str]:
        out: set = set()
        async with self.session.client("s3", **self._kwargs) as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket=self.bucket, Prefix=self.prefix, Delimiter="/",
            ):
                for cp in page.get("CommonPrefixes") or []:
                    p = cp.get("Prefix", "")
                    if p.startswith(self.prefix):
                        topic = p[len(self.prefix):].rstrip("/")
                        if topic:
                            out.add(topic)
        return sorted(out)

    async def ping(self) -> Dict[str, Any]:
        try:
            async with self.session.client("s3", **self._kwargs) as s3:
                await s3.head_bucket(Bucket=self.bucket)
            return {"ok": True, "kind": "s3", "bucket": self.bucket,
                    "endpoint": self.endpoint or "aws"}
        except Exception as e:
            return {"ok": False, "kind": "s3", "bucket": self.bucket,
                    "endpoint": self.endpoint or "aws", "error": str(e)}

    async def aclose(self) -> None: pass


# ----------------------------------------------------------------------
# torch-tensor convenience helpers (used by recursive/persistence.py)
# ----------------------------------------------------------------------

async def save_tensor(store, topic_id: str, name: str, tensor) -> None:
    """Serialize a torch tensor and write via the store."""
    import torch  # local import — torch is a heavy dep
    buf = io.BytesIO()
    torch.save(tensor, buf)
    await store.save_blob(topic_id, name, buf.getvalue())


async def load_tensor(store, topic_id: str, name: str):
    import torch
    data = await store.load_blob(topic_id, name)
    return torch.load(io.BytesIO(data))
