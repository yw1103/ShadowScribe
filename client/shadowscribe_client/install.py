"""``ss setup`` — point the editor at the server's MCP endpoint.

There is very little to do here, and that is the point. The MCP server runs *on
the server*, next to the memory it serves, so the desktop needs no package, no
daemon and no proxy — only a URL:

    { "mcpServers": { "shadowscribe": {
        "url": "http://<host>:18080/mcp",
        "headers": { "Authorization": "Bearer <SS_TOKEN>" } } } }

An earlier version of this module installed a stdio MCP server into the user's
Python and registered *that* as the command, which then proxied back to this same
server over HTTP. Every step was avoidable, and it made the laptop a participant
in a system whose whole design is that the laptop is a thin reader.

One small file is still written locally: a static instruction telling the agent
*when* to call the tools. Registering an MCP server answers "where", not "when" —
an agent that never calls a tool has been given nothing.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import inject as inject_mod

#: Name the editor sees. Must match what the server calls its MCP server.
SERVER_NAME = "shadowscribe"

#: Where the MCP endpoint is mounted, relative to the server base URL.
MCP_PATH = "/mcp"


@dataclass
class ActionResult:
    ok: bool
    label: str
    detail: str


@dataclass
class SetupReport:
    actions: list[ActionResult] = field(default_factory=list)
    user_rules_hint: str = ""
    mcp_url: str = ""

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


def mcp_url(endpoint: str) -> str:
    return endpoint.rstrip("/") + MCP_PATH


# ------------------------------------------------------------------- writing


def _merge_mcp_config(path: Path, url: str, token: str) -> ActionResult:
    """Add (or refresh) our entry, preserving every other server.

    The user's file already holds unrelated servers, often with credentials
    inline — rewriting it wholesale would destroy that, so this reads, merges,
    backs up, and writes. A file it cannot parse is reported, not overwritten.
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
        entry = {"url": url, "headers": {"Authorization": f"Bearer {token}"}}
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
    endpoint: str,
    token: str,
    project_root: Path | None = None,
    global_scope: bool = True,
) -> SetupReport:
    """Point the editors at the server's MCP endpoint and write the instruction."""
    root = project_root or Path.cwd()
    url = mcp_url(endpoint)
    report = SetupReport(mcp_url=url)

    if not token:
        report.add(False, "MCP · 配置", "token 为空，无法写入 Authorization 头")
    else:
        report.actions.append(_merge_mcp_config(cursor_mcp_path(), url, token))
        report.actions.append(_merge_mcp_config(claude_desktop_config_path(), url, token))

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
