"""Reserve stdout for the MCP stdio transport.

Over stdio, every byte on stdout belongs to JSON-RPC. Libraries do not know
that: when the browser is missing, Camoufox downloads it and reports progress
through ``print`` and ``rich``, both of which target ``sys.stdout``. Those lines
reach the client as invalid protocol messages.

The transport reads ``sys.stdout.buffer`` when it starts, so the guard keeps
that attribute pointing at the real stream and sends every text write to
stderr. Protocol bytes are unchanged.
"""
from __future__ import annotations

import sys


class _ProtocolSafeStdout:
    """Text output goes to stderr; ``buffer`` stays the real stdout."""

    def __init__(self, protocol, diagnostics):
        self.buffer = protocol.buffer
        self._diagnostics = diagnostics

    def write(self, text):
        return self._diagnostics.write(text)

    def __getattr__(self, name):
        return getattr(self._diagnostics, name)


def reserve_stdout_for_protocol() -> None:
    """Route text written to ``sys.stdout`` to stderr. Safe to call twice."""
    stdout = sys.stdout
    if isinstance(stdout, _ProtocolSafeStdout) or not hasattr(stdout, "buffer"):
        return
    sys.stdout = _ProtocolSafeStdout(stdout, sys.stderr)
