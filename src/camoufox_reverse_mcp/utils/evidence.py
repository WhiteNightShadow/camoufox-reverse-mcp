"""Generic helpers for moving page-side values onto disk safely.

These exist so a tool can hand back a *file* instead of a large value in the
conversation: page blobs (bytecode, device state, cookie headers, extracted
scripts) are routinely multi-kilobyte and get truncated or balloon the context
when returned inline.

Nothing here is target-specific; any project can reuse it.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

MAX_EVIDENCE_BYTES = 50 * 1024 * 1024  # 50 MiB safety ceiling
URL_PREVIEW_CHARS = 200


def sanitize_surrogates(text: str) -> tuple[str, int]:
    """Merge valid UTF-16 surrogate pairs and replace lone surrogates with U+FFFD."""
    if not isinstance(text, str):
        return text, 0
    try:
        text.encode("utf-8")
        return text, 0
    except UnicodeEncodeError:
        pass
    out: list[str] = []
    replaced = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        code = ord(ch)
        if 0xD800 <= code <= 0xDBFF:
            if i + 1 < n:
                low = ord(text[i + 1])
                if 0xDC00 <= low <= 0xDFFF:
                    combined = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    out.append(chr(combined))
                    i += 2
                    continue
            out.append("\ufffd")
            replaced += 1
        elif 0xDC00 <= code <= 0xDFFF:
            out.append("\ufffd")
            replaced += 1
        else:
            out.append(ch)
        i += 1
    return "".join(out), replaced


def resolve_output_path(path: str, *, overwrite: bool = False, create_parents: bool = True) -> Path:
    """Expand, normalize, and validate a destination regular-file path.

    Refuses empty paths, directories, symlinks (including dangling symlinks),
    FIFOs/devices, and existing files unless ``overwrite`` is true.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("output path is empty")
    expanded = os.path.abspath(os.path.expandvars(os.path.expanduser(path.strip())))
    target = Path(expanded)

    try:
        st = os.lstat(target)
    except FileNotFoundError:
        st = None

    if st is not None:
        if stat.S_ISLNK(st.st_mode):
            raise ValueError(f"{target} is a symlink; refusing to follow symlinks for output_file")
        if stat.S_ISDIR(st.st_mode):
            raise IsADirectoryError(f"{target} is a directory, not a file")
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"{target} is not a regular file")
        if not overwrite:
            raise FileExistsError(f"{target} already exists; pass overwrite=true to replace it")

    if create_parents and target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    return target


def write_bytes_evidence(
    path: str,
    data: bytes,
    *,
    overwrite: bool = False,
    mode: int = 0o644,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Atomically write raw bytes to a regular file and return path/size/digest metadata."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(f"expected bytes, got {type(data).__name__}")
    raw = bytes(data)
    limit = MAX_EVIDENCE_BYTES if max_bytes is None else max_bytes
    if len(raw) > limit:
        raise ValueError(f"output payload is {len(raw)} bytes, exceeding limit of {limit} bytes")

    target = resolve_output_path(path, overwrite=overwrite, create_parents=True)

    if not overwrite:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
        )
        fd = os.open(str(target), flags, mode)
        try:
            view = memoryview(raw)
            written = 0
            while written < len(raw):
                written += os.write(fd, view[written:])
        except Exception:
            try:
                os.close(fd)
            finally:
                try:
                    os.unlink(target)
                except OSError:
                    pass
            raise
        else:
            os.close(fd)
    else:
        # Re-check leaf via lstat right before atomic temp-file replacement so
        # we never follow a symlink or clobber a directory/FIFO.
        try:
            st = os.lstat(target)
        except FileNotFoundError:
            st = None
        if st is not None:
            if stat.S_ISLNK(st.st_mode):
                raise ValueError(f"{target} is a symlink; refusing to overwrite symlink")
            if stat.S_ISDIR(st.st_mode):
                raise IsADirectoryError(f"{target} is a directory, not a file")
            if not stat.S_ISREG(st.st_mode):
                raise ValueError(f"{target} is not a regular file")

        fd, tmp_path = tempfile.mkstemp(prefix=".mcp_ev_", dir=str(target.parent))
        try:
            try:
                os.chmod(tmp_path, mode)
            except OSError:
                pass
            view = memoryview(raw)
            written = 0
            while written < len(raw):
                written += os.write(fd, view[written:])
            os.close(fd)
            fd = -1
            os.replace(tmp_path, target)
        except Exception:
            if fd != -1:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    return {
        "path": str(target),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "encoding": "binary",
    }


def write_text_evidence(
    path: str,
    text: str,
    *,
    overwrite: bool = False,
    encoding: str = "utf-8",
    mode: int = 0o644,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Write UTF-8/text safely (sanitizing lone UTF-16 surrogates) and return metadata."""
    if not isinstance(text, str):
        raise TypeError(f"expected str, got {type(text).__name__}")
    clean_text, surrogates_replaced = sanitize_surrogates(text)
    data = clean_text.encode(encoding, "replace")
    meta = write_bytes_evidence(
        path,
        data,
        overwrite=overwrite,
        mode=mode,
        max_bytes=max_bytes,
    )
    meta["encoding"] = encoding
    meta["chars"] = len(clean_text)
    if surrogates_replaced:
        meta["lone_surrogates_replaced"] = surrogates_replaced
    return meta


def preview(text: str, limit: int = 120) -> str | None:
    """Small, JSON-safe single-line preview for tool responses (never the whole payload)."""
    if not isinstance(text, str):
        return None
    limit = max(0, int(limit))
    clean_text, _ = sanitize_surrogates(text)
    flat = clean_text.replace("\r", " ").replace("\n", " ").strip()
    if not flat:
        return ""
    if limit == 0:
        return "..."
    return flat[:limit] + ("..." if len(flat) > limit else "")


def normalize_url_text(raw_url: Any) -> str:
    """Coerce an entry URL to a UTF-8/surrogate-safe string."""
    if raw_url is None:
        return ""
    clean_url, _ = sanitize_surrogates(raw_url if isinstance(raw_url, str) else str(raw_url))
    return clean_url


def url_preview_fields(raw_url: Any, limit: int = URL_PREVIEW_CHARS) -> dict[str, Any]:
    """Bounded URL preview plus truncation metadata for request summaries."""
    clean_url = normalize_url_text(raw_url)
    limit = max(0, int(limit))
    truncated = len(clean_url) > limit
    return {
        "url": clean_url[:limit] + "…" if truncated else clean_url,
        "url_truncated": truncated,
        "url_length": len(clean_url),
    }
