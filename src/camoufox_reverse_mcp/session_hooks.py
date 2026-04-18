"""Transparent tool-call archiver for domain sessions."""
from __future__ import annotations

import functools
import time
from typing import Any, Callable

from .domain_session import get_store


def _summarize_args(args: tuple, kwargs: dict, max_str: int = 200) -> dict:
    def _trim(v):
        if isinstance(v, str) and len(v) > max_str:
            return v[:max_str] + f"...({len(v)} chars)"
        if isinstance(v, (list, dict)) and len(str(v)) > max_str:
            return str(v)[:max_str] + "...(truncated)"
        if callable(v):
            return f"<callable {getattr(v, '__name__', 'anon')}>"
        return v
    out = {}
    for i, a in enumerate(args):
        out[f"_arg{i}"] = _trim(a)
    for k, v in kwargs.items():
        out[k] = _trim(v)
    return out


def _summarize_result(r: Any, max_str: int = 400) -> dict:
    if not isinstance(r, dict):
        s = str(r)
        return {"type": type(r).__name__, "preview": s[:max_str]}
    out = {}
    for k, v in r.items():
        if k == "error":
            out[k] = str(v)[:max_str]
        elif isinstance(v, (list, dict)):
            out[k + "_size"] = len(v)
        elif isinstance(v, str):
            out[k] = v[:max_str] + ("..." if len(v) > max_str else "")
        else:
            out[k] = v
    return out


def session_record_tool_call(tool_name: str):
    """Decorator that auto-archives tool calls to the active domain session."""
    def decorator(fn: Callable):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            t0 = time.time()
            err = None
            result = None
            try:
                result = await fn(*args, **kwargs)
                return result
            except Exception as e:
                err = str(e)
                raise
            finally:
                try:
                    store = get_store()
                    if (store.active is not None
                            and store.active.active_run_id is not None
                            and store.active.recording.get("tool_calls", True)):
                        store.record("tool_calls", {
                            "tool": tool_name,
                            "args": _summarize_args(args, kwargs),
                            "result_summary": (_summarize_result(result)
                                               if result else None),
                            "duration_ms": int((time.time() - t0) * 1000),
                            "error": err,
                        })
                except Exception:
                    pass
        return async_wrapper
    return decorator
