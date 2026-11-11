from __future__ import annotations

import pathlib
import tempfile
import time

from ..server import browser_manager, mcp

_TRACES: dict[str, object] = {}


@mcp.tool()
async def trace_session(
    action: str = "status",
    screenshots: bool = True,
    snapshots: bool = True,
    sources: bool = True,
    path: str | None = None,
) -> dict:
    """Record a Playwright trace of the current browser context.

    Firefox has no CDP, so there is no pause/step debugger here. A trace is the
    supported way to get a replayable record: DOM snapshots, network activity,
    console output and source locations, packaged as a `.zip` for
    `trace.playwright.dev` or `playwright show-trace`.

    Args:
        action: "start" begin recording, "stop" write the archive, "status"
            report whether a recording is active.
        screenshots: Include screenshots in the trace (default True).
        snapshots: Include DOM snapshots (default True).
        sources: Include source files (default True).
        path: Destination `.zip` for action="stop"; defaults to a temp file.

    Returns:
        dict with the recording state, and on stop the archive path and size.
    """
    if action not in ("start", "stop", "status"):
        return {"error": "action must be one of: start, stop, status"}

    ctx = browser_manager.contexts.get("default")
    if ctx is None:
        return {"error": "no browser context available; launch the browser first"}

    if action == "status":
        return {"recording": bool(_TRACES)}

    if action == "start":
        if _TRACES:
            return {"recording": True, "note": "a trace is already being recorded"}
        try:
            await ctx.tracing.start(screenshots=screenshots, snapshots=snapshots, sources=sources)
        except Exception as e:
            return {"error": str(e)}
        _TRACES["started_at"] = time.time()
        return {"recording": True, "options": {"screenshots": screenshots,
                                               "snapshots": snapshots, "sources": sources}}

    if not _TRACES:
        return {"error": "no trace is being recorded; call action='start' first"}

    dest = pathlib.Path(path) if path else (
        pathlib.Path(tempfile.gettempdir()) / f"mcp-trace-{int(time.time())}.zip")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        await ctx.tracing.stop(path=str(dest))
    except Exception as e:
        _TRACES.clear()
        return {"error": str(e)}
    _TRACES.clear()
    size = dest.stat().st_size if dest.exists() else 0
    return {
        "recording": False,
        "path": str(dest),
        "bytes": size,
        "open_with": "https://trace.playwright.dev/ (drag the zip there) or "
                     f"`playwright show-trace {dest}`",
    }
