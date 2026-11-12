"""Build the JSVMP probes and install them in the page main world.

Both templates in hooks/ are function expressions that return
{installed, banner, ...}. They do not print: console output from the main
world reaches page code that wraps console methods.
"""
from __future__ import annotations

import json
import os

HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")
DEFAULT_PROXY_OBJECTS = ["navigator", "screen", "history",
                         "localStorage", "sessionStorage", "performance"]
READ_WITH = 'evaluate_js("window.__mcp_jsvmp_log || []", world="main")'


def _template(name: str) -> str:
    with open(os.path.join(HOOKS_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def _fill_shared_placeholders(template: str, script_url: str, max_entries: int) -> str:
    """Fill the placeholders both templates share.

    json.dumps turns script_url into a complete JS string literal, so quotes,
    backslashes and line breaks in it cannot end the string early.
    """
    return (template
        .replace("'{{SCRIPT_URL}}'", json.dumps(script_url))
        .replace("{{MAX_ENTRIES}}", str(int(max_entries))))


def transparent_probe(script_url: str = "", max_entries: int = 10000) -> str:
    """One JS expression that installs the transparent probe and returns its info."""
    template = _template("jsvmp_transparent_hook.js")
    return "(" + _fill_shared_placeholders(template, script_url, max_entries) + ")()"


def proxy_probe(
    script_url: str = "",
    max_entries: int = 10000,
    track_calls: bool = True,
    track_props: bool = True,
    track_reflect: bool = True,
    proxy_objects: list[str] | None = None,
) -> str:
    """One JS expression that installs the proxy probe and returns its info."""
    objects = DEFAULT_PROXY_OBJECTS if proxy_objects is None else proxy_objects
    js = (_fill_shared_placeholders(_template("jsvmp_hook.js"), script_url, max_entries)
        .replace("{{TRACK_CALLS}}", "true" if track_calls else "false")
        .replace("{{TRACK_PROPS}}", "true" if track_props else "false")
        .replace("{{TRACK_REFLECT}}", "true" if track_reflect else "false")
        .replace("'{{PROXY_OBJECTS}}'", json.dumps(json.dumps(objects))))
    return "(" + js + ")()"


def main_world_init_script(probe: str, native: bool = True, label: str = "JSVMP") -> str:
    """Init script that installs a probe in the main world of each new document.

    native=True (Camoufox launched with main_world_eval): the mw: prefix makes
    Camoufox run the script in the page main world before page scripts, whatever
    the page's CSP says. A build without that channel drops mw: init scripts, so
    callers pass native=False there: the script then runs in the isolated world,
    sees window.wrappedJSObject and installs through window.wrappedJSObject.eval.
    A CSP without 'unsafe-eval' blocks that fallback, and the reason goes to the
    console.

    The banner is printed at document start, before page code could wrap
    console methods.
    """
    source = "JSON.stringify(" + probe + ")"
    return (
        ("mw:" if native else "") + "(function () {\n"
        "    var info;\n"
        "    try {\n"
        "        if (typeof window.wrappedJSObject === 'undefined') {\n"
        "            info = (function () { return " + probe + "; })();\n"
        "        } else {\n"
        f"            info = JSON.parse(window.wrappedJSObject.eval({json.dumps(source)}));\n"
        "        }\n"
        "    } catch (e) {\n"
        f"        console.log('[{label}] main-world install failed: ' + (e && e.message));\n"
        "        return;\n"
        "    }\n"
        "    if (info && info.banner) console.log(info.banner);\n"
        "})();"
    )
