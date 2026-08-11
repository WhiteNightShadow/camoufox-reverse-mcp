"""Helpers for updating Camoufox's chunked ``CAMOU_CONFIG`` environment."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any


CONFIG_KEY = "CAMOU_CONFIG"
CONFIG_CHUNK_PREFIX = "CAMOU_CONFIG_"
WINDOWS_CHUNK_SIZE = 2047
OTHER_CHUNK_SIZE = 32767


def _default_chunk_size(os_name: str | None = None) -> int:
    platform_name = os.name if os_name is None else os_name
    return WINDOWS_CHUNK_SIZE if platform_name == "nt" else OTHER_CHUNK_SIZE


def _decode_config(value: object, source: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ValueError(f"{source} must be a JSON string")
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source} does not contain valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{source} must contain a JSON object")
    return decoded


def _chunk_items(env: Mapping[str, object]) -> list[tuple[int, str, object]]:
    """Return the exact contiguous chunk sequence consumed by MaskConfig."""
    chunks: list[tuple[int, str, object]] = []
    index = 1
    while True:
        key = f"{CONFIG_CHUNK_PREFIX}{index}"
        if key not in env:
            break
        chunks.append((index, key, env[key]))
        index += 1
    return chunks


def merge_camou_config_env(
    env: Mapping[str, Any],
    updates: Mapping[str, Any],
    *,
    chunk_size: int | None = None,
) -> dict[str, Any]:
    """Merge values into Camoufox config and rebuild its environment chunks.

    Camoufox serializes one JSON document across ``CAMOU_CONFIG_1..n``.  Each
    chunk is only a string fragment and cannot be decoded independently.  This
    helper mirrors the browser's numeric, contiguous read order, applies the
    update atomically, and replaces all old config variables so stale chunks
    cannot shadow the new document.
    """

    resolved_chunk_size = _default_chunk_size() if chunk_size is None else chunk_size
    if resolved_chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")

    chunks = _chunk_items(env)

    if chunks:
        try:
            parts: list[str] = []
            for _, key, value in chunks:
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be a string")
                parts.append(value)
            payload = "".join(parts)
            config = _decode_config(payload, "CAMOU_CONFIG chunks")
        except ValueError as exc:
            raise ValueError(f"Unable to decode Camoufox config: {exc}") from exc
    elif CONFIG_KEY in env:
        try:
            config = _decode_config(env[CONFIG_KEY], CONFIG_KEY)
        except ValueError as exc:
            raise ValueError(f"Unable to decode Camoufox config: {exc}") from exc
    else:
        config = {}

    merged = dict(config)
    merged.update(updates)

    # ASCII escaping makes character and byte boundaries identical, avoiding
    # partial UTF-8 sequences when environment chunks are passed to a process.
    payload = json.dumps(merged, ensure_ascii=True, separators=(",", ":"))

    result = {
        key: value
        for key, value in env.items()
        if key != CONFIG_KEY and not _is_config_chunk_key(key)
    }
    for offset in range(0, len(payload), resolved_chunk_size):
        index = offset // resolved_chunk_size + 1
        result[f"{CONFIG_CHUNK_PREFIX}{index}"] = payload[
            offset : offset + resolved_chunk_size
        ]
    return result


def _is_config_chunk_key(key: str) -> bool:
    if not key.startswith(CONFIG_CHUNK_PREFIX):
        return False
    suffix = key[len(CONFIG_CHUNK_PREFIX) :]
    return suffix.isdigit() and int(suffix) > 0
