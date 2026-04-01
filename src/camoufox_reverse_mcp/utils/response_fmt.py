from __future__ import annotations

import json
from typing import Any


def format_response(data: Any, max_length: int = 50000) -> str:
    """Format a response value for MCP output.

    Handles serialization of complex objects and truncation of
    overly large responses.

    Args:
        data: The data to format.
        max_length: Maximum response length before truncation.

    Returns:
        JSON-formatted string.
    """
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(data)

    if len(text) > max_length:
        text = text[:max_length] + f"\n... (truncated, total {len(text)} chars)"
    return text


def truncate_str(s: str, max_len: int = 5000) -> str:
    """Truncate a string to max_len with an indicator."""
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"... ({len(s)} chars total)"
