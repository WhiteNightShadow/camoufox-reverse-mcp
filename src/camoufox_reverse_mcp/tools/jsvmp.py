from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time

from ..server import mcp, browser_manager
from ..utils.jsvmp_hooks import (
    HOOKS_DIR,
    READ_WITH,
    main_world_init_script,
    proxy_probe,
    transparent_probe,
)
from ..utils.worlds import evaluate_in_world, native_main_world_available

ABSENCE_NOTE = (
    "No entry for an API means this probe did not observe it, not that the page "
    "did not call it. Confirm with hook_function(world='main') on the method, or "
    "with instrumentation(action='install') on the script."
)
FRAMES_NOTE = (
    "With persistent=True the probe also starts in every new same-origin frame, "
    "including about:blank iframes the page creates, and logs into the top "
    "frame's window.__mcp_jsvmp_log with frame_url/frame_depth. Cross-origin "
    "frames keep their own log (read it with evaluate_js(frame_url=...))."
)


def _proxy_coverage(info: dict, track_calls: bool, track_props: bool, track_reflect: bool) -> dict:
    records = []
    if track_calls:
        records.append("calls made through Function.prototype.call/apply/bind (fn_call/fn_apply/fn_bind)")
        records.append("Date.now, performance.now and Math.random (api_call)")
    if track_reflect:
        records.append("Reflect.apply/get/set/construct")
    proxied = info.get("proxied") or []
    if track_props and proxied:
        records.append("get/set/has of properties on the proxied globals: " + ", ".join(map(str, proxied)))
    return {
        "records": records,
        "replaced": info.get("replaced") or [],
        "proxied": proxied,
        "does_not_record": [
            "direct method calls such as ctx.measureText(...) or gl.getExtension(...): "
            "they do not go through call/apply",
            "objects that are not proxied globals (canvas, WebGL, document, element and "
            "audio objects) and objects returned from a proxied global",
            "code that saved references before the probe was installed",
        ],
        "absence": ABSENCE_NOTE,
        "frames": FRAMES_NOTE,
    }


def _transparent_coverage(info: dict) -> dict:
    by_owner = info.get("tappedByOwner") or {}
    return {
        "records": ["getter reads on these prototypes: " + ", ".join(
            f"{owner} ({count})" for owner, count in by_owner.items())],
        "getters_by_owner": by_owner,
        "does_not_record": [
            "method calls of any kind (getExtension, measureText, toDataURL, ...)",
            "getters on other prototypes (HTMLCanvasElement, CanvasRenderingContext2D, "
            "WebGLRenderingContext, Element, ...) and own data properties",
            "code that saved references before the probe was installed",
        ],
        "absence": ABSENCE_NOTE,
        "frames": FRAMES_NOTE,
    }


@mcp.tool()
async def hook_jsvmp_interpreter(
    script_url: str = "",
    persistent: bool = True,
    mode: str = "proxy",
    track_calls: bool = True,
    track_props: bool = True,
    track_reflect: bool = True,
    proxy_objects: list[str] | None = None,
    max_entries: int = 10000,
) -> dict:
    """Install a JSVMP runtime probe in the page main world.

    The probe runs where page scripts run, so their property reads and calls
    are recorded. Read the log with
    evaluate_js("window.__mcp_jsvmp_log || []", world="main"); the default
    isolated world cannot see it. remove_hooks() restores what the probe replaced.
    Each event has a seq; window.__mcp_jsvmp_meta.dropped counts events lost to
    max_entries. Same-origin frames (including about:blank iframes created and
    removed by the page) log into the top frame's log with frame_url/frame_depth.
    The result's `coverage` lists what this probe records and what it cannot
    see: a missing entry means "not observed", not "not called".

    mode="proxy" wraps Function.prototype.apply/call/bind, Reflect.*,
    Date.now, performance.now and Math.random, and puts Proxies on globals
    (navigator, screen, etc.). Page code can detect all of this, so RS/AK-style
    signature-based anti-bot will notice; use instrumentation(action='install')
    (source-level rewrite) for those. mode="transparent" only replaces getters
    on prototypes (Navigator, Screen, Document, ...). It changes less, but the
    getters are no longer the originals: Function.prototype.toString.call(getter)
    shows JS source.

    IMPORTANT — timing for sync-loaded SDKs (e.g. webmssdk):
        JSVMP interpreters capture native references at startup via closures.
        If you install hooks AFTER the SDK has loaded, the SDK's closures
        already hold the original (un-hooked) references — your hooks will
        never fire. You MUST install hooks BEFORE navigate():
          1. launch_browser()
          2. hook_jsvmp_interpreter(mode='transparent', persistent=True)
          3. navigate("https://www.douyin.com/...")
        If already navigated, call instrumentation(action='reload') after
        installing hooks to force a page reload with hooks active.

    Args:
        script_url: Target script URL substring for stack filtering.
        persistent: Survive navigation (default True).
        mode: "proxy" (full coverage, detectable) or "transparent" (replaces getters only).
        track_calls, track_props, track_reflect: Only for mode="proxy".
        proxy_objects: Objects to proxy (default: navigator, screen, etc.).
        max_entries: Log buffer cap (default 10000).

    Returns:
        dict with status, mode, world, read_with and install details.
    """
    try:
        page = await browser_manager.get_active_page()
        if mode == "transparent":
            probe = transparent_probe(script_url, max_entries)
            script_name = f"jsvmp_transparent:{script_url or 'all'}"
        elif mode == "proxy":
            probe = proxy_probe(script_url, max_entries, track_calls, track_props,
                                track_reflect, proxy_objects)
            script_name = f"jsvmp_probe:{script_url or 'all'}"
        else:
            return {"error": f"unknown mode '{mode}', use 'proxy' or 'transparent'"}

        # v1.0.1: detect if page already loaded (timing issue)
        page_already_loaded = page.url and page.url != "about:blank"
        install_error = None
        try:
            info, backend, backend_warning = await evaluate_in_world(
                page, probe, "main", script_is_function=False)
        except Exception as e:
            install_error, backend = e, None
        if persistent:
            # The init script takes the same channel the immediate install could use.
            native = (backend == "camoufox_native" if backend
                      else await native_main_world_available(page))
            await browser_manager.add_persistent_script(
                script_name, main_world_init_script(probe, native=native))
        if install_error is not None:
            return {"status": "partial", "mode": mode, "world": "main",
                    "warning": f"Evaluate failed: {install_error}", "persistent": persistent}
        info = info if isinstance(info, dict) else {}

        result = {
            "status": "instrumented", "mode": mode,
            "script_url": script_url or "(all)", "persistent": persistent,
            "world": "main", "execution_backend": backend,
            "data_location": "window.__mcp_jsvmp_log",
            "read_with": READ_WITH,
        }
        if info.get("installed") is False:
            result["already_installed"] = True
        else:
            result["coverage"] = (
                _transparent_coverage(info) if mode == "transparent"
                else _proxy_coverage(info, track_calls, track_props, track_reflect))
        warnings = [backend_warning] if backend_warning else []
        if mode == "transparent":
            if "tapped" in info:
                result["getters_tapped"] = info["tapped"]
                result["getters_seen"] = info["total"]
            if page_already_loaded:
                warnings.append(
                    "Hooks installed on already-loaded page. If the target "
                    "SDK (e.g. webmssdk) was loaded BEFORE this call, it "
                    "likely captured native references at startup (closure "
                    "capture) and won't trigger your hooks. Call "
                    "instrumentation(action='reload') or re-navigate to "
                    "force SDK re-init with hooks in place."
                )
        else:
            result["warning"] = "proxy mode is detectable by RS/AK-style anti-bot."
            # Failures on about:blank (e.g. storage) say nothing about the target page.
            if info.get("proxyFailures") and page_already_loaded:
                warnings.append("Could not proxy: " + ", ".join(map(str, info["proxyFailures"])))
            if page_already_loaded:
                warnings.insert(0, result.pop("warning"))
                warnings.append(
                    "Hooks installed on already-loaded page. If the target "
                    "SDK was loaded BEFORE this call, it likely captured "
                    "native references at startup (closure capture) and "
                    "won't trigger your hooks. Call "
                    "instrumentation(action='reload') to re-trigger."
                )
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return {"error": str(e)}


ENV_SECTIONS = ("navigator", "screen", "window", "css", "canvas", "webgl", "audio",
                "storage", "iframe", "timing")


def _env_capture_script() -> str:
    with open(os.path.join(HOOKS_DIR, "env_capture.js"), "r", encoding="utf-8") as f:
        return f.read()


@mcp.tool()
async def compare_env(
    properties: list[str] | None = None,
    output_path: str | None = None,
    sections: list[str] | None = None,
) -> dict:
    """Collect the browser environment for comparison with Node.js/jsdom.

    Values are read in the isolated world, i.e. what the browser provides
    without page-script patches.

    Without output_path it returns a summary: navigator and screen basics,
    canvas (toDataURL hash, measureText), WebGL (vendor/renderer, extension
    count), audio (OfflineAudioContext fingerprint, sample rate), timing and
    misc, plus `properties` evaluated as expressions.

    With output_path it writes an environment bundle (JSON) and returns its
    path, size and a short summary: every Navigator/Screen getter with plugins
    and mimeTypes, window metrics and global names, getComputedStyle names and
    values, CSS declaration prototype names with enumerable flags and the
    for-in count, the full canvas toDataURL, WebGL parameters/extensions/
    shader precisions, audio, storage keys with value lengths (never values,
    never cookie values), a hidden about:blank iframe compared with the top
    window (added and removed again; a MutationObserver sees it), timing and
    Intl. Apply it in Node/jsdom with the skill's env-bundle template.

    Args:
        properties: Extra expressions to evaluate, e.g. ["navigator.oscpu"].
        output_path: File for the full bundle; omit for the summary.
        sections: Limit the bundle to these sections (default all):
            navigator, screen, window, css, canvas, webgl, audio, storage,
            iframe, timing.

    Returns:
        dict with categorized environment data, or the bundle's path/summary.
    """
    try:
        if sections:
            unknown = [name for name in sections if name not in ENV_SECTIONS]
            if unknown:
                return {"error": f"unknown sections {unknown}; choose from {list(ENV_SECTIONS)}"}
        page = await browser_manager.get_active_page()
        opts = {"full": bool(output_path), "sections": list(sections or []),
                "custom": list(properties or [])}
        data = await page.evaluate(_env_capture_script(), opts)
        canvas = data.get("canvas") if isinstance(data, dict) else None
        if isinstance(canvas, dict) and isinstance(canvas.get("data_url"), str):
            # crypto.subtle cannot hash isolated-world data; hash it here.
            canvas["data_url_sha256"] = hashlib.sha256(canvas["data_url"].encode("utf-8")).hexdigest()
            if not output_path:
                canvas.pop("data_url")
        if not output_path:
            return data
        bundle = {
            "schema": "camoufox-reverse-mcp/env-bundle@1",
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "world": "isolated",
            "page": (data.get("window") or {}).get("location"),
            **data,
        }
        path = pathlib.Path(output_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(bundle, ensure_ascii=False, indent=1)
        path.write_text(text, encoding="utf-8")
        nav = data.get("navigator") or {}
        canvas = data.get("canvas") or {}
        webgl = data.get("webgl") or {}
        css = data.get("css") or {}
        return {
            "output_path": str(path),
            "bytes": len(text.encode("utf-8")),
            "sections": [k for k in ENV_SECTIONS if k in data],
            "summary": {
                "userAgent": (nav.get("userAgent") or {}).get("value"),
                "navigator_getters": len(nav),
                "css_props": len(css.get("props") or []),
                "css_for_in_count": css.get("for_in_count"),
                "canvas_data_url_length": canvas.get("data_url_length"),
                "webgl_renderer": webgl.get("unmasked_renderer") or webgl.get("renderer"),
                "webgl_extensions": webgl.get("extension_count"),
                "iframe_differs_from_top": (data.get("iframe") or {}).get("differs_from_top"),
            },
            **({"errors": data["errors"]} if data.get("errors") else {}),
        }
    except Exception as e:
        return {"error": str(e)}
