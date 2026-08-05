"""MCP server exposing the simulated mailbox.

Why MCP at the tool boundary: every exposed tool has a unique name, a
description and an input schema, which is exactly the contract the benchmark
needs — and it makes the tool surface model-independent. Any MCP client can
drive the same simulator that the in-process harness drives, so a result is a
property of the agent, not of this Python package.

The server is a thin adapter. All behaviour lives in ``callbench.simulator``;
adding logic here would let the MCP path and the in-process path diverge, and a
benchmark whose two execution surfaces disagree measures neither.

Run::

    python -m mcp_server.server --fixture fixture_std_201

Safety: this process can only reach the in-memory simulator. There is no
network client, no credential lookup and no provider SDK anywhere in its import
graph.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from callbench.schemas import get_catalogue
from callbench.simulator import build_fixture, invoke
from callbench.simulator.store import MailboxStore, ToolError

REFUSED_TOOL_PREFIXES = ("gmail.", "outlook.", "smtp.", "imap.")


class SimulatedMailboxServer:
    """Serves one catalogue against one mailbox fixture."""

    def __init__(self, fixture: str, catalogue_name: str, current_time: str) -> None:
        self.catalogue = get_catalogue(catalogue_name)
        self.store: MailboxStore = build_fixture(fixture)
        self.current_time = current_time
        self.fixture = fixture

    def list_tools(self) -> list[dict[str, Any]]:
        return self.catalogue.as_prompt_payload()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name.startswith(REFUSED_TOOL_PREFIXES):
            raise ToolError(
                f"{name!r} names a real mail provider. Real email operations are "
                "disabled in benchmark mode.",
                kind="refused",
            )
        if name not in self.catalogue:
            raise ToolError(f"unknown tool: {name}", kind="unknown_tool")

        before = self.store.snapshot()
        before_hash = self.store.state_hash()
        result = invoke(self.store, self.catalogue.canonical(name), arguments, self.current_time)
        after = self.store.snapshot()
        return {
            "result": result,
            "state": {
                "before_hash": before_hash,
                "after_hash": self.store.state_hash(),
                "changed_resources": MailboxStore.diff(before, after),
            },
        }

    def reset(self) -> dict[str, Any]:
        self.store = build_fixture(self.fixture)
        return {"fixture": self.fixture, "state_hash": self.store.state_hash()}


async def _serve_mcp(server: SimulatedMailboxServer) -> int:  # pragma: no cover - requires mcp
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError:
        print(
            "the `mcp` package is not installed; install it with "
            "`pip install 'callbench[mcp]'`, or use --stdio-json for the "
            "dependency-free line protocol",
            file=sys.stderr,
        )
        return 1

    app: Server = Server("callbench-email-simulator")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name=t["name"], description=t["description"], inputSchema=t["input_schema"])
            for t in server.list_tools()
        ]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            payload = server.call_tool(name, arguments)
        except ToolError as exc:
            payload = {"error": {"kind": exc.kind, "message": str(exc)}}
        return [TextContent(type="text", text=json.dumps(payload, sort_keys=True))]

    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())
    return 0


def _serve_json_lines(server: SimulatedMailboxServer) -> int:
    """A dependency-free line protocol, for CI and for debugging by hand.

    One JSON object per line in, one per line out:
    ``{"op": "list_tools"}`` | ``{"op": "call", "name": ..., "arguments": {...}}``
    | ``{"op": "reset"}``.
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            op = request.get("op")
            if op == "list_tools":
                response: dict[str, Any] = {"tools": server.list_tools()}
            elif op == "call":
                response = server.call_tool(request["name"], request.get("arguments", {}))
            elif op == "reset":
                response = server.reset()
            else:
                response = {"error": {"kind": "bad_request", "message": f"unknown op {op!r}"}}
        except ToolError as exc:
            response = {"error": {"kind": exc.kind, "message": str(exc)}}
        except Exception as exc:  # noqa: BLE001 - the protocol must not die on bad input
            response = {"error": {"kind": "internal", "message": str(exc)}}
        print(json.dumps(response, sort_keys=True), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulated email MCP server.")
    parser.add_argument("--fixture", default="fixture_std_201")
    parser.add_argument("--catalogue", default="catalogue_v1")
    parser.add_argument("--current-time", default="2026-08-05T09:00:00+00:00")
    parser.add_argument(
        "--stdio-json",
        action="store_true",
        help="use the dependency-free JSON-lines protocol instead of MCP",
    )
    args = parser.parse_args(argv)

    server = SimulatedMailboxServer(args.fixture, args.catalogue, args.current_time)
    if args.stdio_json:
        return _serve_json_lines(server)
    return asyncio.run(_serve_mcp(server))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
