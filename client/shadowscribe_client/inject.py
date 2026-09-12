"""Context injection — writing the brief into an editor's rule file.

This is 原则三 (零赘述上下文贯通) made concrete. Instead of the user pasting a
summary into every new chat, the brief is materialised once into the file the
editor already reads at session start.

Every write is idempotent and bounded by explicit markers, so re-running ``ss
inject`` updates the block in place and never appends a second copy. Content
outside the markers is never touched.
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
description: 影书 ShadowScribe — 主人现实世界的因果上下文
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
    "cursor": Target(
        "cursor", ".cursor/rules/shadowscribe.mdc", "Cursor", CURSOR_FRONTMATTER
    ),
    "claude": Target("claude", "CLAUDE.md", "Claude Code / Desktop"),
    "agents": Target("agents", "AGENTS.md", "AGENTS.md convention (Codex, Amp, …)"),
    "copilot": Target(
        "copilot", ".github/copilot-instructions.md", "GitHub Copilot"
    ),
    "windsurf": Target("windsurf", ".windsurfrules", "Windsurf"),
    "cline": Target("cline", ".clinerules", "Cline / Roo"),
    "gemini": Target("gemini", "GEMINI.md", "Gemini CLI"),
}

PREAMBLE = """\
# 现实上下文（影书 ShadowScribe 自动注入）

以下内容由影书从主人最近的现实对话中静默沉淀，**不是用户手写的**。
请把它当作已经发生的既定背景：其中的承诺、决策与因果都已经成立，
直接基于它开展工作，不要反问用户「你刚才说的是什么」。

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
