"""Generic helpers for moving page-side values onto disk.

These exist so a tool can hand back a *file* instead of a large value in the
conversation: page blobs (bytecode, device fingerprints, cookie headers, whole
request bodies) are routinely multi-kilobyte and get truncated or balloon the
context when returned inline.

Nothing here is target-specific; any project can reuse it.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


def resolve_output_path(path: str, *, overwrite: bool = False) -> Path:
    """Expand and validate a destination path.

    Refuses to replace an existing file unless ``overwrite`` is true, so an
    accidental repeat call cannot silently destroy an earlier capture.
    """
    if not path or not str(path).strip():
        raise ValueError("output path is empty")
    target = Path(os.path.expanduser(str(path)))
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"{target} already exists; pass overwrite=true to replace it"
        )
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    return target


def write_bytes_evidence(path: str, data: bytes, *, overwrite: bool = False) -> dict[str, Any]:
    """Write raw bytes and return path/size/digest metadata."""
    target = resolve_output_path(path, overwrite=overwrite)
    target.write_bytes(data)
    return {
        "path": str(target),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encoding": "binary",
    }


def write_text_evidence(
    path: str,
    text: str,
    *,
    overwrite: bool = False,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Write text with an explicit encoding and return path/size/digest metadata."""
    if not isinstance(text, str):
        raise TypeError(f"expected str, got {type(text).__name__}")
    data = text.encode(encoding, "replace")
    meta = write_bytes_evidence(path, data, overwrite=overwrite)
    meta["encoding"] = encoding
    meta["chars"] = len(text)
    return meta


def preview(text: str, limit: int = 120) -> str | None:
    """Small, safe preview for tool responses (never the whole payload)."""
    if not isinstance(text, str):
        return None
    flat = text.replace("\r", " ").replace("\n", " ").strip()
    if not flat:
        return ""
    return flat[:limit] + ("..." if len(flat) > limit else "")
