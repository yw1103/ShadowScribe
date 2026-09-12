"""Tests for the server-hosted MCP surface.

This endpoint is what lets the desktop be a thin reader — the editor connects to
the server directly, so the quality of these tools *is* the desktop experience.
An agent sees only the tool names and descriptions; a missing or thin description
silently removes a capability from the model's reach.
"""

from __future__ import annotations

import asyncio

import pytest

from shadowscribe import mcp_surface

pytest.importorskip("mcp", reason="the mcp extra is not installed")


def _tools(server):
    result = server.list_tools()
    return asyncio.run(result) if asyncio.iscoroutine(result) else result


@pytest.fixture(scope="module")
def server():
    return mcp_surface.build_mcp_server()


def test_expected_tools_are_exposed(server):
    names = {t.name for t in _tools(server)}
    assert names == {
        # sensory layer — ShadowScribe's own SQLite. causal-memory has none of this.
        "get_reality_context",
        "list_open_commitments",
        "search_reality",
        "get_timeline",
        # memory layer — causal-memory, in-process on the server
        "search_memory",
        "causal_directory",
    }


def test_every_tool_has_a_description_that_says_when_to_call_it(server):
    """The docstrings are the product surface.

    A model will not call a tool whose purpose it cannot read, so a short or
    missing description disables the capability just as surely as a bug.
    """
    for tool in _tools(server):
        assert tool.description, f"{tool.name} has no description"
        assert len(tool.description) > 30, f"{tool.name}'s description is too thin"


def test_instructions_steer_the_agent_away_from_interrogating_the_user(server):
    text = server.instructions or ""
    assert "get_reality_context" in text
    assert "不要反问" in text


def test_tools_return_readable_text_on_an_empty_store(server):
    """A cold store must produce a usable answer, not an exception."""
    for name, args in (
        ("get_reality_context", {"hours": 24, "max_tokens": 400}),
        ("list_open_commitments", {"status": "open"}),
        ("get_timeline", {}),
        ("search_reality", {"query": "anything", "limit": 3}),
        ("search_memory", {"query": "anything", "limit": 3}),
        ("causal_directory", {"limit": 5}),
    ):
        result = asyncio.run(server.call_tool(name, args))
        content = result[0] if isinstance(result, tuple) else result.content
        text = content[0].text if isinstance(content, (list, tuple)) else str(content)
        assert text is not None
        assert "[shadowscribe]" not in text, f"{name} reported an internal error: {text[:120]}"


def test_get_timeline_rejects_a_bad_day(server):
    result = asyncio.run(server.call_tool("get_timeline", {"day": "08-01-2026"}))
    content = result[0] if isinstance(result, tuple) else result.content
    text = content[0].text if isinstance(content, (list, tuple)) else str(content)
    assert "YYYY-MM-DD" in text


def test_list_open_commitments_coerces_a_bad_status(server):
    """A model may pass anything; it must not blow up the call."""
    result = asyncio.run(server.call_tool("list_open_commitments", {"status": "urgent-ish"}))
    content = result[0] if isinstance(result, tuple) else result.content
    text = content[0].text if isinstance(content, (list, tuple)) else str(content)
    assert "没有" in text or "项" in text


# ------------------------------------------------------------------- auth


class _Recorder:
    """Minimal ASGI app that records whether it was reached."""

    def __init__(self):
        self.reached = False

    async def __call__(self, scope, receive, send):
        self.reached = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run(app, headers, scope_type="http"):
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": scope_type, "headers": headers, "method": "POST", "path": "/mcp"}
    asyncio.run(app(scope, receive, send))
    return sent


def test_bearer_auth_rejects_a_missing_token():
    inner = _Recorder()
    app = mcp_surface.BearerAuth(inner, "secret")
    sent = _run(app, [])

    assert not inner.reached
    assert sent[0]["status"] == 401
    assert b"www-authenticate" in dict(sent[0]["headers"])


def test_bearer_auth_rejects_a_wrong_token():
    inner = _Recorder()
    app = mcp_surface.BearerAuth(inner, "secret")
    sent = _run(app, [(b"authorization", b"Bearer nope")])

    assert not inner.reached
    assert sent[0]["status"] == 401


def test_bearer_auth_accepts_the_right_token():
    inner = _Recorder()
    app = mcp_surface.BearerAuth(inner, "secret")
    sent = _run(app, [(b"authorization", b"Bearer secret")])

    assert inner.reached
    assert sent[0]["status"] == 200


def test_bearer_auth_accepts_the_header_fallback():
    """Same X-SS-Token fallback the rest of the API accepts."""
    inner = _Recorder()
    app = mcp_surface.BearerAuth(inner, "secret")
    _run(app, [(b"x-ss-token", b"secret")])
    assert inner.reached


def test_bearer_auth_passes_non_http_scopes_through():
    """Lifespan scopes must not be gated, or the session manager never starts."""
    inner = _Recorder()
    app = mcp_surface.BearerAuth(inner, "secret")
    _run(app, [], scope_type="lifespan")
    assert inner.reached


def test_auth_is_open_when_no_token_is_configured():
    """Dev mode: an empty token means the rest of the API is open too."""
    inner = _Recorder()
    app = mcp_surface.BearerAuth(inner, "")
    _run(app, [])
    assert inner.reached
