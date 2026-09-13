"""CLI-facing behaviour that is easy to break and hard to notice.

The Windows console encoding bug this covers made ``ss brief`` raise
``UnicodeEncodeError`` on the first emoji — the tool was unusable on the platform
it is most likely to run on, and no unit test would have caught it because CI is
Linux-only and UTF-8 by default.
"""

from __future__ import annotations

import argparse
import io

import pytest

from shadowscribe_client import cli


class _NoReconfigure(io.StringIO):
    """A stream that does not implement reconfigure (an old/odd wrapper)."""


def test_configure_stdio_does_not_raise(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    monkeypatch.setattr(cli.sys, "stderr", io.StringIO())
    cli._configure_stdio()  # must not blow up


def test_configure_stdio_tolerates_streams_without_reconfigure(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdout", _NoReconfigure())
    monkeypatch.setattr(cli.sys, "stderr", _NoReconfigure())
    cli._configure_stdio()


def test_configure_stdio_tolerates_none_streams(monkeypatch):
    """pythonw.exe has no stdout at all."""
    monkeypatch.setattr(cli.sys, "stdout", None)
    monkeypatch.setattr(cli.sys, "stderr", None)
    cli._configure_stdio()


def test_configure_stdio_survives_a_reconfigure_that_raises(monkeypatch):
    class _Angry(io.StringIO):
        def reconfigure(self, **kwargs):
            raise OSError("no")

    monkeypatch.setattr(cli.sys, "stdout", _Angry())
    monkeypatch.setattr(cli.sys, "stderr", _Angry())
    cli._configure_stdio()


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["brief", "--help"],
        ["inject", "--help"],
        ["commitments", "--help"],
    ],
)
def test_help_exits_cleanly(argv):
    """`--help` must work without a server and without touching the filesystem."""
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 0


def test_unknown_command_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["definitely-not-a-command"])
    assert exc.value.code != 0


def test_missing_argument_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        cli.main(["search"])  # requires a query
    assert exc.value.code != 0


@pytest.mark.parametrize(
    "command",
    [
        "login",
        "setup",
        "status",
        "brief",
        "commitments",
        "search",
        "timeline",
        "upload",
        "recordings",
        "inject",
        "doctor",
    ],
)
def test_every_documented_command_exists(command):
    """docs/operations.md lists these; keep the two in sync."""
    parser = cli.build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    assert command in subparsers.choices


def test_there_is_no_local_mcp_command():
    """MCP is served by the ShadowScribe server, not by this package.

    `ss mcp` used to spawn a stdio server that proxied back to the server over
    HTTP — a redundant hop that forced a pip install onto the desktop. If it ever
    comes back it should be a deliberate decision, not an accident.
    """
    parser = cli.build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    assert "mcp" not in subparsers.choices


def test_doctor_help_works_without_a_server():
    with pytest.raises(SystemExit) as exc:
        cli.main(["doctor", "--help"])
    assert exc.value.code == 0


# ------------------------------------------------------------------ mcp probe


class _Cfg:
    endpoint = "http://host:18080"


def _fake_httpx(monkeypatch, *, status=None, raises=None, record=None):
    """Stand in for httpx.stream, which is a context manager, not a call."""
    import types

    class _Resp:
        def __init__(self) -> None:
            self.status_code = status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def stream(method, url, **kwargs):
        if record is not None:
            record.append((method, url, kwargs))
        if raises is not None:
            raise raises
        return _Resp()

    module = types.SimpleNamespace(stream=stream)
    monkeypatch.setitem(__import__("sys").modules, "httpx", module)


def test_mcp_probe_does_not_wait_for_a_body(monkeypatch):
    """GET /mcp is an SSE channel: it sends 200 and never finishes the body.

    This is not hypothetical — a plain ``httpx.get`` here raised ReadTimeout and
    `ss doctor` told the user their MCP server was down while Cursor was happily
    using it. The probe must therefore stream and read only the status.
    """
    record: list = []
    _fake_httpx(monkeypatch, status=200, record=record)

    ok, detail = cli._probe_mcp(_Cfg())

    assert ok, detail
    assert "200" in detail
    assert record and record[0][0] == "GET", "probe should read the status, not the body"


def test_mcp_probe_flags_a_build_without_the_extra(monkeypatch):
    _fake_httpx(monkeypatch, status=404)
    ok, detail = cli._probe_mcp(_Cfg())
    assert not ok
    assert "404" in detail


def test_mcp_probe_treats_a_token_gate_as_success(monkeypatch):
    _fake_httpx(monkeypatch, status=401)
    ok, detail = cli._probe_mcp(_Cfg())
    assert ok, detail
    assert "token" in detail


def test_mcp_probe_reports_transport_failure(monkeypatch):
    _fake_httpx(monkeypatch, raises=RuntimeError("boom"))
    ok, detail = cli._probe_mcp(_Cfg())
    assert not ok
    assert "不可达" in detail
