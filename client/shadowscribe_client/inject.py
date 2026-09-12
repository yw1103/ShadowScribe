"""Writing ShadowScribe's instructions into an editor's config.

There are two different things this module can write, and conflating them was a
design mistake worth spelling out:

**A static instruction** (:func:`render_instruction`) tells the agent that the
``shadowscribe`` MCP server exists and that it should pull context from it. It
contains no memories, so it never goes stale and never needs to be re-run. This
is the mechanism that makes the desktop imperceptible (原则三).

**A snapshot** (:func:`render_block`) embeds the current context card itself. It
*is* useful — for clients with no MCP support, or for pasting into a web chat —
but it expires the moment the world moves on, so it must never be the daily
driver. It was, until a user pointed out that being told to run a command every
morning is the opposite of zero-friction.

Both are written idempotently, bounded by explicit markers. Content outside the
markers is never touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

BEGIN = "<!-- SHADOWSCRIBE:BEGIN -->"
END = "<!-- SHADOWSCRIBE:END -->"
_BLOCK = re.compile(rf"{re.escape(BEGIN)}.*?{re.escape(END)}", re.DOTALL)

CURSOR_FRONTMATTER = """\
---
description: 影书 ShadowScribe — 现实上下文按需拉取
alwaysApply: true
---
"""


@dataclass(frozen=True)
class Target:
    key: str
    path: str
    label: str
    frontmatter: str = ""
    note: str = ""


TARGETS: dict[str, Target] = {
    "cursor": Target("cursor", ".cursor/rules/shadowscribe.mdc", "Cursor", CURSOR_FRONTMATTER),
    "claude": Target("claude", "CLAUDE.md", "Claude Code / Desktop"),
    "agents": Target("agents", "AGENTS.md", "AGENTS.md convention (Codex, Amp, …)"),
    "copilot": Target("copilot", ".github/copilot-instructions.md", "GitHub Copilot"),
    "windsurf": Target("windsurf", ".windsurfrules", "Windsurf"),
    "cline": Target("cline", ".clinerules", "Cline / Roo"),
    "gemini": Target("gemini", "GEMINI.md", "Gemini CLI"),
}

#: Instruction text. Kept deliberately short: it is prepended to *every* request,
#: so it competes for context with the user's actual work. It must say what the
#: server is, when to call it, and the one behaviour that matters most — do not
#: interrogate the user for background the memory already holds.
INSTRUCTION = """\
# 影书 ShadowScribe · 现实上下文

这台机器上运行着 `shadowscribe` MCP 服务，它保存着主人**现实世界**（会议、电话、
线下对话）里达成的决策、做出的承诺和因果关系。这些内容用户不会、也不该再复述。

**开始处理任务前，先调用 `get_reality_context`。**
- 问"我还欠谁什么"、"待办" → `list_open_commitments`
- 提到具体的人/项目/事件 → `search_reality`
- 问"今天/昨天干了什么" → `get_timeline`

用户的指令通常很短、缺背景 —— 背景不在他脑子里等你追问，而在影书里。
**不要反问"你指的是什么"，先拉上下文。**
"""

#: Same instruction, minus the tool-name specifics that only apply to MCP clients.
#: Used for the global Claude Code memory file, which also carries plain notes.
CLAUDE_GLOBAL_HEADING = "# 影书 ShadowScribe"


def render_instruction() -> str:
    """The static, never-stale rule body. Safe to write once and forget."""
    return f"{BEGIN}\n{INSTRUCTION}\n{END}\n"


#: Wrapper for the *snapshot* variant only. It has to make clear that the block
#: below is a point-in-time capture — otherwise a reader (human or model) may
#: treat a week-old card as current.
PREAMBLE = """\
# 现实上下文（影书 ShadowScribe 快照）

以下内容是**某个时刻的快照**，由影书从主人最近的现实对话中静默沉淀，不是用户手写的。
请把它当作已发生的既定背景，直接基于它开展工作，不要反问用户「你刚才说的是什么」。

> 注意：这是快照，可能已经过时。若配置了 `shadowscribe` MCP，请改用
> `get_reality_context` 获取实时上下文。

"""


def render_block(brief: str, *, target: Target) -> str:
    body = PREAMBLE + brief.strip() + "\n"
    return f"{BEGIN}\n{body}{END}\n"


def inject(path: Path, block: str, *, frontmatter: str = "") -> str:
    """Insert or replace the managed block. Returns ``created`` | ``updated`` | ``unchanged``."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(f"{frontmatter}{block}", encoding="utf-8")
        return "created"

    existing = path.read_text(encoding="utf-8")
    if _BLOCK.search(existing):
        updated = _BLOCK.sub(lambda _m: block.rstrip("\n"), existing)
        if updated == existing:
            return "unchanged"
        path.write_text(updated, encoding="utf-8")
        return "updated"

    separator = "" if existing.endswith("\n") else "\n"
    path.write_text(f"{existing}{separator}\n{block}", encoding="utf-8")
    return "updated"


def remove(path: Path) -> str:
    """Strip the managed block, leaving everything else intact."""
    if not path.exists():
        return "absent"
    existing = path.read_text(encoding="utf-8")
    updated = _BLOCK.sub("", existing).rstrip() + "\n"
    if updated == existing:
        return "absent"
    path.write_text(updated, encoding="utf-8")
    return "removed"


def resolve_targets(keys: list[str] | None, root: Path | None = None) -> list[tuple[Target, Path]]:
    root = root or Path.cwd()
    chosen = keys or ["cursor", "claude"]
    out: list[tuple[Target, Path]] = []
    for key in chosen:
        target = TARGETS.get(key)
        if target is None:
            raise KeyError(f"unknown target {key!r}; known: {', '.join(sorted(TARGETS))}")
        out.append((target, root / target.path))
    return out


def detect_targets(root: Path | None = None) -> list[tuple[Target, Path]]:
    """Targets that already look present in this repo — the default for ``--auto``."""
    root = root or Path.cwd()
    found: list[tuple[Target, Path]] = []

    if (root / ".cursor").exists():
        found.append((TARGETS["cursor"], root / TARGETS["cursor"].path))
    if (root / ".github").exists():
        found.append((TARGETS["copilot"], root / TARGETS["copilot"].path))
    for key in ("claude", "agents", "gemini", "windsurf", "cline"):
        target = TARGETS[key]
        path = root / target.path
        if path.exists():
            found.append((target, path))
    return found
