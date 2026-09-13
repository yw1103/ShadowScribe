"""Documentation must not drift away from the code.

This is not documentation coverage — it is a set of constraints that caught real
staleness. Every failure mode below was present in this repository at some point:

* a documented command that no longer exists (``ss mcp``),
* a documented endpoint shape that no longer exists
  (``{"command": "ss", "args": ["mcp"]}``, MCP resources),
* a documented API path that the app does not serve,
* a relative link to a file that was renamed or removed.

Prose describing *why* is not checked here; only the mechanical facts that a
reader would copy and paste.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Every markdown file that is part of the shipped docs.
DOCS = sorted(
    p
    for p in list(REPO.glob("*.md"))
    + list((REPO / "docs").glob("*.md"))
    + list((REPO / "client").glob("*.md"))
    + list((REPO / "server").glob("*.md"))
    if p.is_file()
)

#: Strings that must not reappear. Each describes something removed on purpose, and
#: a doc still recommending it sends the reader down a dead end.
FORBIDDEN = {
    "ss mcp": "MCP is served by the server; there is no local stdio command",
    '"args": ["mcp"]': "MCP is registered by URL, not by command",
    "pending_work_summary": "that tool no longer exists",
    "shadowscribe://brief": "the server exposes no MCP resources",
    "shadowscribe://commitments": "the server exposes no MCP resources",
    "client[mcp]": "the client has no mcp extra",
    "'.[mcp,dev]'": "the client has no mcp extra",
}

#: Phrases meaning the line *is explaining the removal*, which is legitimate — the
#: CHANGELOG and the "what changed" notes have to be able to name the thing.
REMOVAL_MARKERS = ("没有", "删除", "移除", "去掉", "不再", "removed", "delete", "no longer", "gone")

#: `ss <word>` occurrences that are not subcommands.
NOT_SUBCOMMANDS = {"setup-client", "is", "will", "can", "and", "or", "to"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_docs_exist():
    assert len(DOCS) >= 10, [p.name for p in DOCS]


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_forbidden_stale_strings(path: Path):
    offenders: list[str] = []
    in_removed_section = False
    for lineno, line in enumerate(_read(path).splitlines(), start=1):
        if line.startswith("#"):
            # A "### Removed" section exists precisely to name what went away.
            in_removed_section = "说明" not in line and any(
                word in line for word in ("Removed", "删除", "移除")
            )
            continue
        if in_removed_section or any(marker in line for marker in REMOVAL_MARKERS):
            continue
        for needle, why in FORBIDDEN.items():
            if needle in line:
                offenders.append(f"{path.relative_to(REPO)}:{lineno} mentions {needle!r} — {why}")
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_relative_links_resolve(path: Path):
    """A renamed file must not leave a dead link behind."""
    broken: list[str] = []
    for label, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", _read(path)):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        clean = target.split("#", 1)[0].strip()
        if not clean:
            continue
        if not (path.parent / clean).resolve().exists():
            broken.append(f"{label} → {target}")
    assert not broken, f"{path.relative_to(REPO)} has dead links: {broken}"


def _documented_ss_commands() -> set[str]:
    found: set[str] = set()
    for path in DOCS:
        for line in _read(path).splitlines():
            # `- [ ] **`ss watch`**` is a roadmap item, not a claim that it exists.
            if line.lstrip().startswith("- [ ]"):
                continue
            for match in re.findall(r"(?<![\w./-])ss\s+([a-z][a-z-]{1,20})", line):
                if match not in NOT_SUBCOMMANDS:
                    found.add(match)
    return found


def test_documented_ss_commands_all_exist():
    """The client test suite already pins the parser; this pins the prose.

    `ss mcp` stayed in the docs for a while after the command was deleted.
    """
    parser_tests = _read(REPO / "client" / "tests" / "test_cli.py")
    known = set(re.findall(r'"([a-z][a-z-]+)"', parser_tests))

    unknown = {cmd for cmd in _documented_ss_commands() if cmd not in known}
    assert not unknown, (
        f"docs reference ss subcommands that do not exist: {sorted(unknown)}. "
        f"Either implement them or fix the docs."
    )


def test_documented_api_paths_are_actually_served():
    """A documented curl that 404s is worse than no documentation.

    Reads the OpenAPI schema rather than walking ``app.routes``: FastAPI 1.x nests
    an ``include_router`` call as an ``_IncludedRouter`` whose own ``path`` is
    ``None`` and which does not expose ``.routes``, so a flat or naive recursive
    scan sees no ``/v1`` paths at all and every documented path looks missing.
    The schema also happens to be exactly the contract the docs describe.
    """
    from shadowscribe.api import create_app

    app = create_app()
    served = set(app.openapi().get("paths", {}))
    assert served, "no paths in the OpenAPI schema — the app failed to build"

    documented: set[str] = set()
    for path in DOCS:
        documented |= set(re.findall(r"(/v1/[a-z0-9/_{}.-]*[a-z0-9_}])", _read(path)))

    # Normalise path params so /v1/recordings/{id} matches however it is spelled.
    def shape(p: str) -> str:
        return re.sub(r"\{[^}]*\}", "{}", p).rstrip("/")

    served_shapes = {shape(p) for p in served}
    missing = {p for p in documented if shape(p) not in served_shapes}
    assert not missing, f"docs document API paths the app does not serve: {sorted(missing)}"


def test_readme_lists_every_mcp_tool():
    """The tool list is the contract the agent sees; keep the README honest."""
    import asyncio

    from shadowscribe.mcp_surface import build_mcp_server

    server = build_mcp_server()
    tools = server.list_tools()
    tools = asyncio.run(tools) if asyncio.iscoroutine(tools) else tools
    names = {t.name for t in tools}

    readme = _read(REPO / "README.md")
    missing = {name for name in names if name not in readme}
    assert not missing, f"README does not mention these MCP tools: {sorted(missing)}"
