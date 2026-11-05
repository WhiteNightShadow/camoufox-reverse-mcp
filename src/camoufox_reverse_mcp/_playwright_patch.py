"""
Startup patch for a Playwright Firefox-driver crash (issue #5).

Some Playwright builds serialize an uncaught page error by reading
``pageError.location.url`` / ``.lineNumber`` / ``.columnNumber`` without a
null check. When a page throws an uncaught error whose ``location`` is
``undefined`` (observed on sites such as arcteryx.com and rei.com), the
Node.js driver process crashes with::

    TypeError: Cannot read properties of undefined (reading 'url')

and the Python side only sees ``Connection closed while reading from the
driver``. This is a bug inside the bundled Playwright driver — not in this
project nor in Camoufox — and it cannot be caught from Python because the
crash happens in the driver process before the event reaches us.

We fix it at startup by adding optional chaining + defaults. The patch is
deliberately conservative:

* It scans every ``*.js`` under the installed Playwright driver (covers both
  the bundled ``coreBundle.js`` layout used by older builds and the
  split-file layout used by newer ones).
* It only rewrites the exact buggy tokens (``pageError.location.url`` etc.),
  which appear *only* in the vulnerable serialization path — so substring
  replacement cannot touch unrelated code.
* It is a no-op on versions that don't contain those tokens. The tokens are
  still present in Playwright 1.62, so this is a real rewrite on current builds.
* It is idempotent (re-running finds nothing to change).
* It replaces files instead of writing through them. uv hardlinks installed
  packages from its global cache on Linux and Windows, so an in-place write
  would change that cached copy for every other project on the machine.
* It never raises: any failure only prints a warning to stderr and startup
  continues normally.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Exact buggy tokens -> safe replacements. Each left-hand string appears only
# in the vulnerable pageError serialization path, so plain substring
# replacement is safe and cannot hit unrelated identifiers.
_REPLACEMENTS = (
    ("pageError.location.url", "pageError.location?.url ?? ''"),
    ("pageError.location.lineNumber", "pageError.location?.lineNumber ?? 0"),
    ("pageError.location.columnNumber", "pageError.location?.columnNumber ?? 0"),
)

# Sentinel token whose presence means "not yet patched". After patching, the
# raw form is gone (it becomes `pageError.location?.url`), so this also drives
# idempotency.
_BUGGY_SENTINEL = "pageError.location.url"


def _driver_lib_root() -> Path | None:
    """Locate <playwright>/driver/package/lib, or None if unavailable."""
    try:
        import playwright

        root = Path(playwright.__file__).parent / "driver" / "package" / "lib"
        return root if root.is_dir() else None
    except Exception:
        return None


def _replace_file(path: Path, text: str) -> None:
    """Swap in new content without touching the old inode."""
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _patch_file(path: Path) -> bool:
    """Patch a single JS file. Returns True only if it was rewritten."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    # Fast path + idempotency: nothing to do if the raw buggy token is absent.
    if _BUGGY_SENTINEL not in text:
        return False
    new_text = text
    for old, repl in _REPLACEMENTS:
        new_text = new_text.replace(old, repl)
    if new_text == text:
        return False
    try:
        _replace_file(path, new_text)
        return True
    except Exception as e:
        # Read-only site-packages, permission issues, etc. — don't crash startup.
        print(
            f"[camoufox-reverse-mcp] could not write Playwright patch to "
            f"{path.name}: {e}",
            file=sys.stderr,
        )
        return False


def patch_playwright_pageerror() -> None:
    """Best-effort startup patch for the Playwright pageError crash.

    Safe to call unconditionally on every launch. Never raises.
    """
    try:
        root = _driver_lib_root()
        if root is None:
            return
        patched: list[str] = []
        for js in root.rglob("*.js"):
            if _patch_file(js):
                patched.append(js.name)
        if patched:
            print(
                "[camoufox-reverse-mcp] patched Playwright pageError crash "
                f"(issue #5) in: {', '.join(sorted(set(patched)))}",
                file=sys.stderr,
            )
    except Exception as e:
        # Absolutely never let the patch break server startup.
        print(
            f"[camoufox-reverse-mcp] Playwright pageError patch skipped: {e}",
            file=sys.stderr,
        )
