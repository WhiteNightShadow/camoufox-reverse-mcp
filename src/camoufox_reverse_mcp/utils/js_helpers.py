from __future__ import annotations

import os


def _read_hook_template(filename: str) -> str:
    """Read a JS hook template file from the hooks/ directory."""
    hooks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")
    filepath = os.path.join(hooks_dir, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def get_font_fallback_script() -> str:
    """Load the CJK font fallback script for cross-OS fingerprinting."""
    return _read_hook_template("font_fallback.js")


def render_trace_template(
    function_path: str,
    max_captures: int = 50,
    log_args: bool = True,
    log_return: bool = True,
    log_stack: bool = False,
) -> str:
    """Render the trace_template.js with the given parameters.

    Args:
        function_path: Full path to the target function.
        max_captures: Maximum number of calls to capture.
        log_args: Whether to log function arguments.
        log_return: Whether to log return values.
        log_stack: Whether to log call stacks.

    Returns:
        Rendered JavaScript code string ready for injection.
    """
    template = _read_hook_template("trace_template.js")
    js = template.replace("{{FUNCTION_PATH}}", function_path)
    js = js.replace("{{MAX_CAPTURES}}", str(max_captures))
    js = js.replace("{{LOG_ARGS}}", "true" if log_args else "false")
    js = js.replace("{{LOG_RETURN}}", "true" if log_return else "false")
    js = js.replace("{{LOG_STACK}}", "true" if log_stack else "false")
    return js
