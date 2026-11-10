from __future__ import annotations

import json
from typing import Any


def wrapped_main_world_script(script: str, *, script_is_function: bool = True) -> str:
    """Build the Firefox Xray fallback for one JSON-safe evaluate expression."""
    invocation = f"({script})()" if script_is_function else f"({script})"
    source = (
        "(async () => { const value = await ("
        + invocation
        + "); return JSON.stringify({__mcp_value: value}); })()"
    )
    return f"""async () => {{
        const mainWindow = window.wrappedJSObject;
        if (!mainWindow || typeof mainWindow.eval !== 'function') {{
            throw new Error('main_world_unavailable: window.wrappedJSObject.eval is unavailable');
        }}
        const encoded = await mainWindow.eval({json.dumps(source)});
        if (typeof encoded !== 'string')
            throw new Error('main_world_serialization_failed');
        return JSON.parse(encoded).__mcp_value;
    }}"""


NATIVE_PROBE = (
    "mw:() => (async () => ({__mcp_native_ok: true, value: await Promise.resolve(null)}))()"
)


async def native_main_world_available(target: Any) -> bool:
    """True when Camoufox's mw: channel works here (launched with main_world_eval)."""
    try:
        probe = await target.evaluate(NATIVE_PROBE)
    except Exception:
        return False
    return isinstance(probe, dict) and probe.get("__mcp_native_ok") is True


def wrapped_main_world_init_script(script: str) -> str:
    """Return an IIFE form suitable for BrowserContext.add_init_script()."""
    return "(" + wrapped_main_world_script(script, script_is_function=False) + ")()"


async def evaluate_in_world(
    target: Any,
    script: str,
    world: str,
    *,
    script_is_function: bool = True,
) -> tuple[Any, str, str | None]:
    """Evaluate through Camoufox's native main-world channel with Firefox fallback.

    Supported Camoufox builds recognize the ``mw:`` prefix when launched
    with ``main_world_eval=True``. Attach mode may point at an older/external
    server, so an explicit wrappedJSObject fallback keeps that configuration
    functional without ever silently falling back to the isolated world.
    """
    if world == "isolated":
        return await target.evaluate(script), "isolated", None
    if world != "main":
        raise ValueError("world must be 'isolated' or 'main'")

    invocation = f"({script})()" if script_is_function else f"({script})"
    native_script = (
        "() => (async () => ({__mcp_native_ok: true, value: await ("
        + invocation
        + ")}))()"
    )
    # Probe without running caller code. Retrying a failed caller expression via
    # another channel can repeat side effects (including page getters and calls
    # that navigated away before Playwright received their result).
    try:
        # Match the real payload's Promise contract. Some older mw: bridges
        # return synchronous values but drop Promise results.
        probe = await target.evaluate(NATIVE_PROBE)
        if not isinstance(probe, dict) or probe.get("__mcp_native_ok") is not True:
            raise RuntimeError("Camoufox main-world channel returned no probe sentinel")
    except Exception as native_error:
        try:
            value = await target.evaluate(
                wrapped_main_world_script(
                    script, script_is_function=script_is_function
                )
            )
            return (
                value,
                "wrappedJSObject",
                "Camoufox native main-world evaluation was unavailable; "
                "used Firefox wrappedJSObject fallback.",
            )
        except Exception as fallback_error:
            raise RuntimeError(
                "main-world evaluation failed via both Camoufox native channel "
                f"and wrappedJSObject fallback: {native_error} / {fallback_error}"
            ) from fallback_error

    native = await target.evaluate("mw:" + native_script)
    if not isinstance(native, dict) or native.get("__mcp_native_ok") is not True:
        raise RuntimeError(
            "Camoufox main-world execution returned no result sentinel; "
            "the expression was not retried because it may have side effects"
        )
    return native.get("value"), "camoufox_native", None
