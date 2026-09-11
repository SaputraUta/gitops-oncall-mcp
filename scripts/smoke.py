#!/usr/bin/env python3
"""End-to-end smoke test against a running gitops-oncall-mcp server.

Exercises every tool category. SAFE to run against production infra — never
calls `confirm_*`, so no destructive action fires. The `propose_*` step is
called with intentionally-invalid input to exercise the validation path.

Usage:
    python scripts/smoke.py [ENV]

ENV defaults to "staging". Set MCP_URL if not http://127.0.0.1:8765/mcp.
Set MCP_BEARER_TOKEN if your server requires auth.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

ENV = sys.argv[1] if len(sys.argv) > 1 else "staging"
URL = os.environ.get("MCP_URL", "http://127.0.0.1:8765/mcp")
TOKEN = os.environ.get("MCP_BEARER_TOKEN", "").strip()


def _short(text: str, max_len: int = 200) -> str:
    text = str(text)
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... [+{len(text) - max_len} chars]"


def _format(result: Any) -> str:
    """FastMCP returns CallToolResult; pull out the content for display."""
    if hasattr(result, "content"):
        parts = []
        for c in result.content:
            if hasattr(c, "text"):
                parts.append(c.text)
            elif hasattr(c, "data"):
                parts.append(f"<image, {len(c.data)} bytes>")
            else:
                parts.append(str(c))
        return _short(" ".join(parts))
    return _short(json.dumps(result, default=str))


async def run() -> int:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    transport = StreamableHttpTransport(URL, headers=headers)

    print(f"=== connecting to {URL} ===")
    fail_count = 0

    async with Client(transport) as client:
        # 1. List tools — proves the server discovered everything we expect
        tools = await client.list_tools()
        names = sorted(t.name for t in tools)
        print(f"\n=== tools/list ({len(tools)} discovered) ===")
        for n in names:
            print(f"  - {n}")

        expected = {
            "ping",
            "get_cpu_usage",
            "get_memory_usage",
            "get_disk_usage",
            "get_error_rate",
            "get_latency_p95",
            "get_active_alerts",
            "search_logs",
            "get_recent_deploys",
            "get_commit_diff",
            "get_file_commits",
            "list_dashboards",
            "list_panels",
            "capture_dashboard",
            "capture_panel",
            "propose_rollback",
            "confirm_rollback",
            "propose_pr_change",
            "confirm_pr_change",
        }
        missing = expected - set(names)
        extra = set(names) - expected
        if missing:
            print(f"\n  ⚠ MISSING: {sorted(missing)}")
            fail_count += 1
        if extra:
            print(f"\n  ℹ extra (not necessarily wrong): {sorted(extra)}")

        # 2. Exercise each category with a real call
        smoke_calls: list[tuple[str, dict]] = [
            ("ping", {}),
            ("get_cpu_usage", {"env": ENV}),
            ("get_memory_usage", {"env": ENV}),
            ("get_disk_usage", {"env": ENV}),
            ("get_error_rate", {"env": ENV}),
            ("get_latency_p95", {"env": ENV}),
            ("get_active_alerts", {}),
            ("search_logs", {"env": ENV, "contains": "error", "minutes": 60, "limit": 3}),
            ("get_recent_deploys", {"env": ENV, "limit": 3}),
            ("list_dashboards", {}),
        ]

        for name, args in smoke_calls:
            print(f"\n=== {name}({args}) ===")
            try:
                result = await client.call_tool(name, args)
                print(_format(result))
            except Exception as e:
                print(f"  ✗ {type(e).__name__}: {e}")
                fail_count += 1

        # 3. Guardrails wire: propose with invalid tag → expect validation error
        print("\n=== propose_rollback (invalid tag — expect validation error) ===")
        try:
            await client.call_tool(
                "propose_rollback",
                {"env": ENV, "target_tag": "DUMMY-WILL-FAIL-VALIDATION"},
            )
            print("  ✗ no error raised — tag validation is broken")
            fail_count += 1
        except Exception as e:
            # Expected: ValueError about tag mismatch comes back as a tool error
            msg = str(e)
            if "doesn't match" in msg or "convention" in msg or "ValueError" in msg:
                print(f"  ✓ validation rejected as expected: {_short(msg, 150)}")
            else:
                print(f"  ⚠ rejected but unexpected message: {_short(msg, 150)}")

    print()
    if fail_count == 0:
        print("=== ✓ all smoke checks passed ===")
        return 0
    print(f"=== ✗ {fail_count} check(s) failed ===")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
