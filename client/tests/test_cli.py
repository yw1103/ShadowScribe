"""CLI-facing behaviour that is easy to break and hard to notice.

The Windows console encoding bug this covers made ``ss brief`` raise
``UnicodeEncodeError`` on the first emoji — the tool was unusable on the platform
it is most likely to run on, and no unit test would have caught it because CI is
Linux-only and UTF-8 by default.
"""

from __future__ import annotations

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
