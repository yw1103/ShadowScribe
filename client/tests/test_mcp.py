"""MCP server wiring tests.

The MCP path is the hardest part of the client to notice breaking: nothing
exercises it until a real editor connects, and by then the failure surfaces as an
opaque "server failed to start" in someone else's UI.

`mcp` 2.x renamed `FastMCP` to `MCPServer`, so these tests pin the dual-version
loader rather than the SDK.
"""

from __future__ import annotations

import asyncio

import pytest

from shadowscribe_client import mcp_server

mcp = pytest.importorskip("mcp", reason="the mcp extra is not installed")


def _list_tools(server):
    """mcp 2.x made the introspection helpers async; 1.x kept them sync."""
    result = server.list_tools()
    return asyncio.run(result) if asyncio.iscoroutine(result) else result


def _list_resources(server):
    result = server.list_resources()
    return asyncio.run(result) if asyncio.iscoroutine(result) else result


def test_server_class_resolves_on_this_sdk():
    cls = mcp_server._server_class()
    assert cls is not None
    assert hasattr(cls, "tool")
    assert hasattr(cls, "resource")
    assert hasattr(cls, "run")


def test_server_class_prefers_the_v2_name_when_present():
    """mcp 2.x is the current release; MCPServer is where FastMCP went."""
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:
        pytest.skip("running against mcp 1.x")
    assert mcp_server._server_class() is MCPServer


def test_build_server_registers_expected_tools():
    server = mcp_server.build_server()
    tools = {t.name for t in _list_tools(server)}
    assert tools == {
        "get_reality_context",
        "list_open_commitments",
        "search_reality",
        "get_timeline",
        "pending_work_summary",
    }
    resources = {str(r.uri) for r in _list_resources(server)}
    assert resources == {"shadowscribe://brief", "shadowscribe://commitments"}


def test_tool_descriptions_explain_when_to_call_them():
    """An agent will not call a memory tool whose purpose it cannot read.

    The docstrings are the product surface here — this asserts they exist and say
    something, because an empty description silently disables the whole feature.
    """
    server = mcp_server.build_server()
    for tool in _list_tools(server):
        assert tool.description, f"{tool.name} has no description"
        assert len(tool.description) > 30, f"{tool.name}'s description is too thin to guide a model"


def test_server_instructions_steer_the_agent_to_the_context_tool():
    server = mcp_server.build_server()
    assert "get_reality_context" in (server.instructions or "")


def test_tools_degrade_gracefully_when_the_server_is_unreachable(monkeypatch):
    """A dead server must produce a readable message, not a stack trace in the
    user's editor."""

    class Dead:
        def brief(self, **_kwargs):
            raise RuntimeError("connection refused")

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(mcp_server, "_client", lambda: Dead())
    server = mcp_server.build_server()

    result = asyncio.run(server.call_tool("get_reality_context", {"hours": 24, "max_tokens": 500}))
    text = result[0].text if isinstance(result, tuple) else result.content[0].text
    assert "无法读取现实上下文" in text
    assert "ss status" in text
