from __future__ import annotations

import re
from typing import Any


def _frame_name(frame: Any) -> str:
    """Return a Playwright frame name without assuming a concrete mock type."""
    value = getattr(frame, "name", "")
    if callable(value):
        return ""
    return str(value or "")


def _frame_url(frame: Any) -> str:
    value = getattr(frame, "url", "")
    if callable(value):
        return ""
    return str(value or "")


def _matches(value: str, pattern: str) -> bool:
    """Match exact text or the documented ``*``/``?`` wildcards only."""
    if any(char in pattern for char in "*?"):
        regex = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
        return re.fullmatch(regex, value) is not None
    return value == pattern


def metadata_matches(
    metadata: dict[str, Any],
    *,
    frame_url: str | None = None,
    frame_name: str | None = None,
    frame_index: int | None = None,
) -> bool:
    """Apply public frame selectors to a JSON-safe metadata record."""
    if frame_url is not None and not _matches(str(metadata.get("url") or ""), frame_url):
        return False
    if frame_name is not None and not _matches(str(metadata.get("name") or ""), frame_name):
        return False
    if frame_index is not None and metadata.get("index") != frame_index:
        return False
    return True


def list_frame_metadata(page: Any) -> list[dict[str, Any]]:
    """Return stable, JSON-safe metadata for the page's current frame tree."""
    frames = list(getattr(page, "frames", []) or [])
    index_by_id = {id(frame): index for index, frame in enumerate(frames)}
    main_frame = getattr(page, "main_frame", None)
    result: list[dict[str, Any]] = []

    for index, frame in enumerate(frames):
        parent = getattr(frame, "parent_frame", None)
        result.append(
            {
                "index": index,
                "url": _frame_url(frame),
                "name": _frame_name(frame),
                "is_main": frame is main_frame or (main_frame is None and index == 0),
                "parent_index": index_by_id.get(id(parent)) if parent is not None else None,
            }
        )
    return result


def resolve_frame(
    page: Any,
    *,
    frame_url: str | None = None,
    frame_name: str | None = None,
    frame_index: int | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Resolve one frame deterministically while preserving the main-page default.

    ``frame_url`` and ``frame_name`` are exact matches unless they contain shell
    wildcards. Multiple selectors are combined. A selector that matches multiple
    frames is rejected instead of silently choosing the wrong execution context.
    """
    selectors_used = frame_url is not None or frame_name is not None or frame_index is not None
    if not selectors_used:
        # Preserve the historical Page.evaluate() path when no selector is used.
        # Page.evaluate is equivalent to main_frame.evaluate, while being friendlier
        # to callers and test doubles that expose no concrete Frame object.
        target = page
        main_frame = getattr(page, "main_frame", None)
        frames = list(getattr(page, "frames", []) or [])
        index = next((i for i, item in enumerate(frames) if item is main_frame), 0)
        return target, {
            "index": index,
            "url": _frame_url(main_frame) or _frame_url(page),
            "name": _frame_name(main_frame),
            "is_main": True,
            "parent_index": None,
        }

    frames = list(getattr(page, "frames", []) or [])
    metadata = list_frame_metadata(page)
    candidates = list(zip(frames, metadata))

    if frame_index is not None:
        if isinstance(frame_index, bool) or frame_index < 0 or frame_index >= len(candidates):
            raise ValueError(
                f"frame_index {frame_index!r} is out of range; page has {len(candidates)} frames"
            )
        candidates = [candidates[frame_index]]

    if frame_url is not None:
        candidates = [item for item in candidates if _matches(item[1]["url"], frame_url)]
    if frame_name is not None:
        candidates = [item for item in candidates if _matches(item[1]["name"], frame_name)]

    if not candidates:
        raise ValueError(
            "frame_not_found: no frame matched "
            f"frame_url={frame_url!r}, frame_name={frame_name!r}, frame_index={frame_index!r}"
        )
    if len(candidates) > 1:
        indexes = [item[1]["index"] for item in candidates]
        raise ValueError(
            f"frame_ambiguous: selectors matched frame indexes {indexes}; add frame_index"
        )
    return candidates[0]


def persistent_frame_guard(
    *,
    frame_url: str | None = None,
    frame_name: str | None = None,
    frame_index: int | None = None,
) -> dict[str, Any] | None:
    """Build a JSON-safe selector used by context init scripts in every frame."""
    if frame_url is None and frame_name is None and frame_index is None:
        return None
    return {"url": frame_url, "name": frame_name, "index": frame_index}
