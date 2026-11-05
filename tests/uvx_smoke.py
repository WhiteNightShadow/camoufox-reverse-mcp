"""Start a stdio MCP server and verify the protocol over raw JSON-RPC.

Usage: python tests/uvx_smoke.py --expect-version <version> -- <server command...>

Uses only the standard library, so it can check an isolated uvx install from
outside that environment. Every stdout line has to parse as JSON: one stray
line from the server or a dependency fails the run.
"""
import argparse
import json
import queue
import subprocess
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-version", required=True)
    parser.add_argument("--min-tools", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("command", nargs="+")
    args = parser.parse_args()

    process = subprocess.Popen(
        args.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1,
    )
    lines, errors = queue.Queue(), []
    threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
    threading.Thread(target=lambda: [errors.append(line) for line in process.stderr], daemon=True).start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def wait_for(request_id):
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            try:
                line = lines.get(timeout=1)
            except queue.Empty:
                if process.poll() is not None:
                    raise SystemExit(f"server exited with {process.returncode}: {''.join(errors)[-1500:]}")
                continue
            try:
                message = json.loads(line)
            except ValueError:
                raise SystemExit(f"non-JSON line on stdout: {line[:200]!r}")
            if message.get("id") == request_id:
                return message
        raise SystemExit(f"timed out waiting for response {request_id}: {''.join(errors)[-800:]}")

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "uvx-smoke", "version": "1"}}})
        server = wait_for(1)["result"]["serverInfo"]
        if server["version"] != args.expect_version:
            raise SystemExit(f"expected version {args.expect_version}, got {server['version']}")

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = wait_for(2)["result"]["tools"]
        if len(tools) < args.min_tools:
            raise SystemExit(f"expected at least {args.min_tools} tools, got {len(tools)}")

        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "check_environment", "arguments": {}}})
        result = wait_for(3)["result"]
        if result.get("isError"):
            raise SystemExit(f"check_environment failed: {result}")
        print(json.dumps({"server": server, "tools": len(tools)}))
    finally:
        process.stdin.close()
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    sys.exit(main())
