"""``ss setup`` — wire the desktop side once, then never think about it again.

The desktop has two independent pieces, and only one of them is per-machine:

* **MCP registration** — tells the editor where the ``shadowscribe`` server is.
  Written once per editor, never changes.
* **The static instruction** — tells the agent *when* to call it. Contains no
  memories, so it never goes stale either.

Neither needs to be re-run. That is the whole point: 原则三 says the user should
never be the transport layer between their own day and their own tools, and a
command you must remember to run every morning makes you exactly that.

Where the instruction can live, and what each is actually worth:

| Location | Scope | Documented? |
|---|---|---|
| ``<project>/.cursor/rules/*.mdc`` | one repo | ✅ Cursor project rules |
| ``<project>/AGENTS.md`` | one repo | ✅ Cursor + Codex + others |
| ``~/.claude/CLAUDE.md`` | all projects | ✅ Claude Code user memory |
| ``~/.cursor/rules/`` | all projects | ❌ undocumented — written as best effort |
| Cursor → Customize → Rules → User Rules | all projects | ✅ but UI-only, no public file |

The last row is why :func:`setup` prints paste-ready text: Cursor's only
*documented* global mechanism lives in its internal store, so the honest answer
is "paste these six lines once", not "I wrote a file, trust me".
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import inject as inject_mod

#: Name the editor sees. Must match what `ss mcp` registers itself as.
SERVER_NAME = "shadowscribe"


def resolve_server_command() -> list[str]:
    """Build an absolute command line for the MCP server.

    Absolute on purpose: an editor launched from the Start Menu inherits a
    different environment than the terminal that installed us, so a bare ``ss``
    frequently is not on its PATH. Prefer the entry point beside *this*
    interpreter — on Linux a PATH lookup can otherwise find iproute2's ``ss``.
    """
    here = Path(sys.executable).parent
    for name in ("ss.exe", "ss"):
        candidate = here / name
        if candidate.is_file():
            return [str(candidate), "mcp"]
    found = shutil.which("ss")
    if found:
        return [found, "mcp"]
    # Last resort: run the module through the interpreter we are running under.
    return [sys.executable, "-m", "shadowscribe_client.cli", "mcp"]


@dataclass
class ActionResult:
    ok: bool
    label: str
    detail: str


@dataclass
class SetupReport:
    actions: list[ActionResult] = field(default_factory=list)
    user_rules_hint: str = ""

    def add(self, ok: bool, label: str, detail: str) -> None:
        self.actions.append(ActionResult(ok, label, detail))

    @property
    def failures(self) -> list[ActionResult]:
        return [a for a in self.actions if not a.ok]


# --------------------------------------------------------------------- paths


def cursor_mcp_path() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def cursor_global_rules_dir() -> Path:
    """Undocumented but harmless: Cursor ignores unknown .mdc files."""
    return Path.home() / ".cursor" / "rules"


def claude_desktop_config_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "Claude"
        / "claude_desktop_config.json"
    )


def claude_code_memory_path() -> Path:
    return Path.home() / ".claude" / "CLAUDE.md"


# ------------------------------------------------------------------- writing


def _merge_mcp_config(path: Path, server_command: list[str]) -> ActionResult:
    """Add (or refresh) our entry, preserving every other server.

    The user's file already holds unrelated servers with credentials in them —
    rewriting it wholesale would destroy that, so this reads, merges, backs up,
    and writes.
    """
    label = f"MCP · {path.name}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if path.exists():
            backup = path.with_suffix(path.suffix + ".bak")
            backup.write_bytes(path.read_bytes())
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return ActionResult(
                    False, label, f"existing file is not valid JSON; backup at {backup.name}"
                )
        if not isinstance(data, dict):
            return ActionResult(False, label, "unexpected top-level JSON type")

        servers = data.setdefault("mcpServers", {})
        existing = servers.get(SERVER_NAME)
        entry = {"command": server_command[0], "args": server_command[1:]}
        if existing == entry:
            return ActionResult(True, label, f"already registered ({len(servers)} servers)")

        servers[SERVER_NAME] = entry
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        verb = "updated" if existing else "added"
        return ActionResult(True, label, f"{verb} → {path}  ({len(servers)} servers total)")
    except OSError as exc:
        return ActionResult(False, label, str(exc))


def _write_instruction(path: Path, frontmatter: str = "") -> ActionResult:
    try:
        outcome = inject_mod.inject(path, inject_mod.render_instruction(), frontmatter=frontmatter)
        return ActionResult(True, f"规则 · {path.name}", f"{outcome} → {path}")
    except OSError as exc:
        return ActionResult(False, f"规则 · {path.name}", str(exc))


# --------------------------------------------------------------------- setup


def user_rules_text() -> str:
    """Paste-ready global rule for Cursor → Customize → Rules → User Rules."""
    return inject_mod.INSTRUCTION.strip()


def setup(
    *,
    project_root: Path | None = None,
    server_command: list[str] | None = None,
    global_scope: bool = True,
) -> SetupReport:
    """Wire MCP + write the static instruction. Idempotent."""
    root = project_root or Path.cwd()
    command = server_command or resolve_server_command()
    report = SetupReport()

    report.actions.append(_merge_mcp_config(cursor_mcp_path(), command))
    report.actions.append(_merge_mcp_config(claude_desktop_config_path(), command))

    # Project-scoped: the only location Cursor documents as guaranteed.
    cursor_target = inject_mod.TARGETS["cursor"]
    report.actions.append(_write_instruction(root / cursor_target.path, cursor_target.frontmatter))
    report.actions.append(_write_instruction(root / inject_mod.TARGETS["agents"].path))

    if global_scope:
        report.actions.append(
            _write_instruction(claude_code_memory_path(), "# 影书 ShadowScribe — 全局助手指令\n")
        )
        report.actions.append(
            _write_instruction(
                cursor_global_rules_dir() / "shadowscribe.mdc", cursor_target.frontmatter
            )
        )

    report.user_rules_hint = user_rules_text()
    return report
