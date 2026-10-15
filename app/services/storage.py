"""Certificate file storage on local disk. Swap this module for S3/GCS in production."""
import os
from pathlib import Path

from app.config import settings


def relative_path(job_id: str, cert_id: str) -> str:
    return f"{job_id}/{cert_id}.pdf"


def absolute_path(rel: str) -> Path:
    """Resolve a stored relative path, refusing anything that escapes the storage dir."""
    root = settings.storage_dir.resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        raise ValueError("Invalid storage path")
    return path


def save(rel: str, content: bytes) -> None:
    """Atomic write: a reader never sees a half-written PDF."""
    path = absolute_path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, path)
