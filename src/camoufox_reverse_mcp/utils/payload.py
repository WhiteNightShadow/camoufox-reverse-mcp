"""Generic helpers for pulling a script string out of a captured response.

A lot of anti-bot SDKs never load as ``<script src>``: the page fetches a JSON
envelope over XHR and ``eval``s one field of it. That content is invisible to
source search (there is no "script" for the debugger to index) and cannot be
instrumented as a route response, so the practical workflow is:

    capture -> extract that field to disk -> patch locally -> serve it back
    with intercept_request(mock_response={"body_file": ...})

These helpers implement the middle step without knowing anything about the
target site.
"""
from __future__ import annotations

import json
from typing import Any


class PayloadExtractError(ValueError):
    """Raised when a captured body cannot yield the requested script string."""


def extract_json_path(text: str, json_path: str) -> Any:
    """Walk a dotted path (``data.data`` / ``items.0.script``) through JSON text."""
    if not isinstance(text, str):
        raise PayloadExtractError(f"expected str body, got {type(text).__name__}")
    try:
        cursor: Any = json.loads(text)
    except Exception as exc:
        raise PayloadExtractError(
            f"body is not JSON ({exc}); pass json_path='' to treat the body as raw script"
        ) from exc
    for part in [p for p in str(json_path).split(".") if p != ""]:
        try:
            if isinstance(cursor, list):
                cursor = cursor[int(part)]
            elif isinstance(cursor, dict):
                cursor = cursor[part]
            else:
                raise PayloadExtractError(
                    f"cannot descend into {type(cursor).__name__} at segment {part!r}"
                )
        except (KeyError, IndexError, ValueError) as exc:
            raise PayloadExtractError(f"path segment {part!r} not found in body") from exc
    if not isinstance(cursor, str):
        raise PayloadExtractError(
            f"path {json_path!r} resolved to {type(cursor).__name__}, not a string"
        )
    if not cursor.strip():
        raise PayloadExtractError(f"path {json_path!r} resolved to an empty string")
    return cursor


def looks_like_script(text: str) -> bool:
    """Cheap heuristic so a wrong path fails loudly instead of writing junk."""
    if not isinstance(text, str):
        return False
    head = text.lstrip()[:400]
    if not head:
        return False
    return any(token in head for token in ("function", "=>", "var ", "const ", "let ", "(", "{"))
