from __future__ import annotations

import json
import time
import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from ..utils.domains import domain_matches

from ..server import mcp, browser_manager
from ..utils.worlds import evaluate_in_world


@mcp.tool()
async def network_capture(
    action: str,
    url_pattern: str = "**/*",
    capture_body: bool = False,
    max_body_size: int = 200000,
    wait_timeout_ms: int = 0,
) -> dict:
    """Unified network capture control (v0.9.0).

    Replaces start_network_capture / stop_network_capture.

    Args:
        action:
          "start"  — begin capturing network events
          "stop"   — stop capturing (buffer retained)
          "clear"  — clear the capture buffer
          "status" — return current capture state
        url_pattern: Glob pattern for "start" (default "**/*" captures all).
        capture_body: For "start" only; capture response bodies (more memory).
        max_body_size: For start; retained characters per response, 0..2000000.
            Playwright still reads the complete response before truncation.
        wait_timeout_ms: For stop; wait up to 30000ms for captured responses.
            New requests stop immediately; unfinished work is reported, not replayed.
            Clear cancels pending work; IDs remain monotonic until browser close.

    Returns:
        dict with action result + current status snapshot.
    """
    if not 0 <= wait_timeout_ms <= 30000:
        return {"error": "wait_timeout_ms must be between 0 and 30000"}
    if action == "start":
        if not 0 <= max_body_size <= 2_000_000:
            return {"error": "max_body_size must be between 0 and 2000000"}
        browser_manager._capture_body_limit = max_body_size
        browser_manager._capturing = True
        browser_manager._capture_pattern = url_pattern
        browser_manager._capture_body = capture_body
        return {"status": "capturing", "pattern": url_pattern,
                "capture_body": capture_body}
    elif action == "stop":
        browser_manager._capturing = False
        deadline = asyncio.get_running_loop().time() + wait_timeout_ms / 1000
        while (browser_manager._request_entries or browser_manager._capture_tasks):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.02, remaining))
        return {"status": "stopped",
                "total_requests": len(browser_manager._network_requests),
                **browser_manager.capture_status()}
    elif action == "clear":
        count = browser_manager.clear_network_capture()
        return {"status": "cleared", "cleared_count": count}
    elif action == "status":
        return browser_manager.capture_status()
    else:
        return {"error": f"unknown action: {action}. Use start/stop/clear/status"}


@mcp.tool()
async def list_network_requests(
    url_filter: str | None = None,
    url_contains_domain: str | None = None,
    method: str | None = None,
    resource_type: str | None = None,
    status_code: int | None = None,
    limit: int | None = None,
    after_id: int | None = None,
) -> list[dict]:
    """List captured network requests with optional filters.

    Args:
        url_filter: Substring filter for request URLs.
        url_contains_domain: Host/subdomain boundary filter (e.g. "example.com").
        method: HTTP method filter (e.g. "GET", "POST").
        resource_type: Resource type filter (e.g. "xhr", "fetch", "script", "document").
        status_code: HTTP status code filter.
        limit: Optional page size (1..2000); omitted preserves the full list.
        after_id: Return IDs greater than this cursor, in capture order.
            Check network_capture(status).dropped_requests for lost history.

    Returns:
        List of request summaries. Legacy size is retained body characters;
        size_unit is characters and body_bytes is the retained decoded entity
        byte count when encoding is known. Not compressed wire bytes.
    """
    try:
        if limit is not None and not 1 <= limit <= 2000:
            return [{"error": "limit must be between 1 and 2000"}]
        if after_id is not None and after_id < 0:
            return [{"error": "after_id must be non-negative"}]
        reqs = list(browser_manager._network_requests)
        if after_id is not None:
            reqs = [r for r in reqs if r["id"] > after_id]
        if url_filter:
            reqs = [r for r in reqs if url_filter in r["url"]]
        if url_contains_domain:
            reqs = [r for r in reqs if domain_matches(urlsplit(r["url"]).hostname or "", url_contains_domain)]
        if method:
            reqs = [r for r in reqs if r["method"].upper() == method.upper()]
        if resource_type:
            reqs = [r for r in reqs if r.get("resource_type") == resource_type]
        if status_code is not None:
            reqs = [r for r in reqs if r.get("status") == status_code]

        if limit is not None:
            reqs = reqs[:limit]
        summaries = []
        for r in reqs:
            body_size = len(r["response_body"]) if r.get("response_body") else 0
            summaries.append({
                "id": r["id"], "url": r["url"][:200], "method": r["method"],
                "status": r.get("status"), "type": r.get("resource_type"),
                "ms": r.get("duration"), "size": body_size,
                "size_unit": "characters",
                "body_bytes": (len(r["response_body"].encode(r["response_body_encoding"]))
                               if r.get("response_body") is not None and r.get("response_body_encoding") in ("utf-8", "latin-1") else None),
                "has_body": r.get("response_body") is not None,
                "state": r.get("state"), "body_state": r.get("body_state"),
                "failure": r.get("failure"),
                "body_truncated": r.get("response_body_capture_truncated", False),
            })
        return summaries
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
async def get_network_request(
    request_id: int,
    include_body: bool = False,
    include_headers: bool = True,
    max_body_size: int = 5000,
) -> dict:
    """Get full details of a specific captured network request.

    Args:
        request_id: The ID of the request (from list_network_requests).
        include_body: Include response body (default False).
        include_headers: Include request/response headers (default True).
        max_body_size: Max chars of body when include_body=True. Pass -1 for unlimited.

    Returns:
        dict with request and response details.
    """
    try:
        for r in browser_manager._network_requests:
            if r["id"] == request_id:
                result = dict(r)
                result["response_body_size_unit"] = "characters"
                if not include_body:
                    body = result.pop("response_body", None)
                    result["response_body_available"] = body is not None
                    if body:
                        result["response_body_size"] = len(body)
                else:
                    body = result.get("response_body")
                    if body is not None:
                        capture_truncated = bool(r.get("response_body_capture_truncated",
                                                       r.get("response_body_truncated", False)))
                        return_truncated = max_body_size >= 0 and len(body) > max_body_size
                        returned = body[:max_body_size] if return_truncated else body
                        result.update(
                            response_body=returned,
                            response_body_truncated=capture_truncated or return_truncated,
                            response_body_capture_truncated=capture_truncated,
                            response_body_return_truncated=return_truncated,
                            response_body_original_size=r.get("response_body_total_size", len(body)),
                            response_body_stored_size=len(body),
                            response_body_size_returned=len(returned),
                        )
                if not include_headers:
                    result.pop("request_headers", None)
                    result.pop("response_headers", None)
                return result
        return {"error": f"Request ID {request_id} not found"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_request_initiator(request_id: int) -> dict:
    """Get the JS call stack that initiated a network request.

    Returns a URL-matched hook stack as an investigation lead, not exact request attribution.
    Repeated/concurrent URLs can match a different invocation; corroborate with captured inputs.
    Requires inject_hook_preset("xhr"/"fetch") BEFORE navigating.

    KNOWN LIMITATIONS (v0.8.1+):
      1. For requests modified by an interceptor registered BEFORE MCP's
         hooks (e.g. SDKs loaded via sync <script>), the initiator will be
         the interceptor's call, not the original business code.
         Workaround: use instrumentation(action='reload').
      2. For fetch on Firefox, Playwright-native initiator is often null.
         Requires inject_hook_preset('fetch', persistent=True).

    Args:
        request_id: The ID of the request.

    Returns:
        dict with url, initiator_stack, source, diagnostics and match_confidence.
        match_confidence is heuristic or unavailable; it never asserts exact identity.
    """
    try:
        target_entry = None
        for r in browser_manager._network_requests:
            if r["id"] == request_id:
                target_entry = r
                break
        if target_entry is None:
            return {"error": f"Request ID {request_id} not found"}

        page = await browser_manager.get_active_page()
        req_url = target_entry["url"]
        escaped_url = json.dumps(req_url)

        # The xhr/fetch presets log in the page main world (v1.10.0).
        result, _, _ = await evaluate_in_world(page, f"""() => {{
            const reqUrl = {escaped_url};
            function searchLogs(logs, type) {{
                if (!logs || !logs.length) return null;
                for (let i = logs.length - 1; i >= 0; i--) {{
                    const log = logs[i];
                    const logUrl = log.url || '';
                    if (!logUrl) continue;
                    if (reqUrl === logUrl || reqUrl.includes(logUrl) || logUrl.includes(reqUrl)) {{
                        return {{
                            url: logUrl, stack: log.stack || null, type: type,
                            method: log.method, headers: log.headers,
                            body: log.body ? String(log.body).substring(0, 2000) : null,
                            timestamp: log.timestamp
                        }};
                    }}
                    try {{
                        const u1 = new URL(reqUrl, location.origin);
                        const u2 = new URL(logUrl, location.origin);
                        if (u1.pathname === u2.pathname && u1.host === u2.host) {{
                            return {{
                                url: logUrl, stack: log.stack || null, type: type,
                                method: log.method, headers: log.headers,
                                body: log.body ? String(log.body).substring(0, 2000) : null,
                                timestamp: log.timestamp
                            }};
                        }}
                    }} catch(e) {{}}
                }}
                return null;
            }}
            const xhrResult = searchLogs(window.__mcp_xhr_log, 'xhr');
            if (xhrResult) return xhrResult;
            const fetchResult = searchLogs(window.__mcp_fetch_log, 'fetch');
            if (fetchResult) return fetchResult;
            const fetchInitLog = window.__mcp_fetch_initiator_log || [];
            for (let i = fetchInitLog.length - 1; i >= 0; i--) {{
                const entry = fetchInitLog[i];
                const logUrl = entry.url || '';
                if (!logUrl) continue;
                if (reqUrl === logUrl || reqUrl.includes(logUrl) || logUrl.includes(reqUrl)) {{
                    return {{ url: logUrl, stack: entry.stack || null, type: 'fetch_hook',
                              method: entry.method, timestamp: entry.ts }};
                }}
                try {{
                    const u1 = new URL(reqUrl, location.origin);
                    const u2 = new URL(logUrl, location.origin);
                    if (u1.pathname === u2.pathname && u1.host === u2.host) {{
                        return {{ url: logUrl, stack: entry.stack || null, type: 'fetch_hook',
                                  method: entry.method, timestamp: entry.ts }};
                    }}
                }} catch(e) {{}}
            }}
            return {{
                url: reqUrl, stack: null, type: 'unknown',
                diagnostics: {{
                    xhr_hook_active: !!window.__mcp_xhr_hooked,
                    fetch_hook_active: !!window.__mcp_fetch_hooked,
                    hint: !window.__mcp_xhr_hooked && !window.__mcp_fetch_hooked
                        ? 'No hooks detected. Call inject_hook_preset("xhr"/"fetch") BEFORE navigating.'
                        : 'Hooks active but no matching URL found in logs.'
                }}
            }};
        }}""", "main")

        source = result.get("type", "unknown")
        return {
            "url": result.get("url"),
            "initiator_stack": result.get("stack"),
            "initiator_type": source,
            "match_confidence": "heuristic" if result.get("stack") else "unavailable",
            "matching_basis": "hook_log_url",
            "evidence_warning": "URL-based hook matching is not exact request attribution; corroborate repeated/concurrent requests with independent input evidence.",
            "source": source,
            "method": result.get("method"),
            "request_headers": result.get("headers"),
            "request_body": result.get("body"),
            "diagnostics": result.get("diagnostics"),
            "diagnostic": (
                {
                    "likely_causes": [
                        "hook registered after SDK (try instrumentation reload)",
                        "request made inside a sync-loaded SDK interceptor",
                        "fetch_hook.js not injected",
                    ],
                    "recommended_action": "Use instrumentation(action='reload') or inject hooks before navigate.",
                }
                if source in ("unknown", None) else None
            ),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def intercept_request(
    url_pattern: str,
    action: str = "log",
    modify_headers: dict | None = None,
    modify_body: str | None = None,
    mock_response: dict | None = None,
) -> dict:
    """Intercept network requests matching a pattern.

    Args:
        url_pattern: URL glob pattern (e.g. "**/api/login*").
        action: "log", "block", "modify", "mock", or "stop" (unroute).
        modify_headers: Headers to add/override (action="modify").
        modify_body: Request body replacement (action="modify").
        mock_response: Dict with "status", "headers", "body" (action="mock").
    """
    try:
        page = await browser_manager.get_active_page()

        if action == "stop":
            if url_pattern:
                await page.unroute(url_pattern)
                return {"status": "stopped", "pattern": url_pattern}
            else:
                await page.unroute("**/*")
                return {"status": "stopped_all"}

        async def handler(route):
            if action == "log":
                browser_manager._console_logs.append({
                    "level": "info",
                    "text": f"[INTERCEPT:log] {route.request.method} {route.request.url}",
                    "timestamp": time.time() * 1000, "location": None,
                })
                await route.continue_()
            elif action == "block":
                await route.abort()
            elif action == "modify":
                overrides = {}
                if modify_headers:
                    overrides["headers"] = {**dict(route.request.headers), **modify_headers}
                if modify_body:
                    overrides["post_data"] = modify_body
                await route.continue_(**overrides)
            elif action == "mock":
                resp = mock_response or {}
                await route.fulfill(
                    status=resp.get("status", 200),
                    headers=resp.get("headers", {"content-type": "application/json"}),
                    body=resp.get("body", "{}"),
                )

        await page.route(url_pattern, handler)
        return {"status": "intercepting", "pattern": url_pattern, "action": action}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def export_network_capture(
    save_path: str,
    include_body: bool = False,
    include_sensitive: bool = False,
    url_filter: str | None = None,
) -> dict:
    """Export a versioned JSON snapshot of this manager's retained capture.

    Args:
        save_path: New local JSON path; existing files are never overwritten.
        include_body: Include captured request/response bodies only together
            with include_sensitive=True. Does not fetch or replay requests.
        include_sensitive: Opt in to original headers, query values and bodies.
            Default masks all header/query values, removes URL credentials and
            fragments, and omits bodies. URL paths are retained: this is not full
            anonymization. Keep original captures out of public repositories.
        url_filter: Optional URL substring filter.

    Returns:
        Path, count, redaction mode and capture status including pending/dropped
        work. For a settled snapshot call network_capture(stop, wait_timeout_ms).
    """
    try:
        if include_body and not include_sensitive:
            return {"error": "include_body requires include_sensitive=True"}
        rows = []
        for entry in browser_manager._network_requests:
            if url_filter and url_filter not in entry["url"]:
                continue
            row = dict(entry)
            if not include_body:
                row.pop("request_post_data", None)
                row.pop("response_body", None)
            if not include_sensitive:
                parts = urlsplit(row["url"])
                row["url"] = urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1],
                    parts.path, urlencode([(key, "[REDACTED]") for key, _ in
                                            parse_qsl(parts.query, keep_blank_values=True)]), ""))
                for field in ("request_headers", "response_headers"):
                    row[field] = {key: "[REDACTED]" for key in (row.get(field) or {})}
                # Transport error strings can contain original URLs.
                for field in ("failure", "headers_error", "body_error", "request_post_data_error"):
                    if row.get(field):
                        row[field] = "[REDACTED]"
            rows.append(row)
        from .. import __version__
        status = browser_manager.capture_status()
        payload = {"schema_version": 1, "mcp_version": __version__,
                   "exported_at": int(time.time() * 1000),
                   "include_sensitive": include_sensitive, "include_body": include_body,
                   "capture": status, "requests": rows}
        if not include_sensitive:
            payload["capture"] = {**status, "pattern": "[REDACTED]"}
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        path = Path(save_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create prevents accidentally destroying another capture.
        with path.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
        return {"status": "exported", "path": str(path), "count": len(rows),
                "schema_version": 1, "redacted": not include_sensitive,
                "capture": payload["capture"]}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
async def compare_network_requests(
    request_ids: list[int],
    include_headers: bool = True,
    include_body: bool = True,
    max_value_chars: int = 200,
    max_fields: int = 100,
) -> dict:
    """Compare 2..10 captured requests without issuing requests or launching a browser.

    Args:
        request_ids: Distinct IDs from list_network_requests in this capture.
        include_headers: Compare available request headers; incompleteness warns.
        include_body: Compare exact request body text plus top-level JSON fields.
        max_value_chars: Preview characters per value (0..2000); digests always
            cover full values in canonical JSON. Each string also includes raw_utf8
            byte length/SHA-256; use that for exact body byte checks. Results may
            contain credentials; keep them private.
        max_fields: Maximum changed rows and constant field names (1..200).

    Returns:
        Changed fields, constant names and completeness limits. Query duplicates,
        value order and raw URL encoding are preserved. Missing differs from null.
        A varying field is evidence, not proof that it participates in signing.
    """
    try:
        if not 2 <= len(request_ids) <= 10 or len(set(request_ids)) != len(request_ids):
            raise ValueError("request_ids must contain 2..10 distinct captured IDs")
        if not 0 <= max_value_chars <= 2000 or not 1 <= max_fields <= 200:
            raise ValueError("max_value_chars must be 0..2000 and max_fields 1..200")
        by_id = {r["id"]: r for r in browser_manager._network_requests}
        missing = [rid for rid in request_ids if rid not in by_id]
        if missing:
            return {"error": "Request IDs unavailable (cleared/evicted or wrong capture)", "missing_ids": missing}
        from ..utils.network_evidence import compare_requests
        return compare_requests([by_id[rid] for rid in request_ids], include_headers=include_headers,
                                include_body=include_body, max_value_chars=max_value_chars, max_fields=max_fields)
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
async def save_response_body(request_id: int, save_path: str, allow_partial: bool = False) -> dict:
    """Save captured response bytes (JS/WASM/JSON/binary) without refetch/replay.

    Args:
        request_id: A captured ID with a completed body (capture_body=True).
        save_path: New file path. Never overwrites existing files.
        allow_partial: Explicitly permit a truncated body; default rejects it.

    Returns:
        Path, saved byte length, SHA-256 and partial flag. Bytes are Playwright's
        decoded HTTP response body (not original compression/wire bytes). If the
        body was missing or truncated, increase the capture limit and collect a
        new sample intentionally; this tool never triggers a new request.
    """
    try:
        entry = next((r for r in browser_manager._network_requests if r["id"] == request_id), None)
        if entry is None:
            return {"error": f"Request ID {request_id} not found"}
        body = entry.get("response_body")
        encoding = entry.get("response_body_encoding")
        if body is None or encoding not in ("utf-8", "latin-1"):
            return {"error": "Captured bytes unavailable; start capture_body=True before the operation and wait for body completion",
                    "body_state": entry.get("body_state")}
        partial = bool(entry.get("response_body_capture_truncated", entry.get("response_body_truncated", False)))
        if partial and not allow_partial:
            return {"error": "Captured body is truncated; use a new intentional capture or explicitly allow_partial",
                    "partial": True, "original_bytes": entry.get("response_body_total_bytes")}
        data = body.encode(encoding)
        import hashlib
        path = Path(save_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
        return {"status": "saved", "request_id": request_id, "path": str(path),
                "bytes_saved": len(data), "original_bytes": entry.get("response_body_total_bytes"),
                "sha256": hashlib.sha256(data).hexdigest(), "partial": partial,
                "content_type": (entry.get("response_headers") or {}).get("content-type")}
    except Exception as exc:
        return {"error": str(exc)}
