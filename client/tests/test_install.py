"""Tests for `ss setup` — the one-shot desktop wiring.

The highest-risk behaviour here is merging into config files the user already
owns: their ``mcp.json`` holds other servers, sometimes with credentials inline.
A bug that overwrites it destroys something this tool was never given permission
to touch, so that is tested first and hardest.

Every path is monkeypatched — these tests must never write to a real home
directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shadowscribe_client import inject as inject_mod
from shadowscribe_client import install


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Redirect every global path into a temp directory."""
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)

    monkeypatch.setattr(install, "cursor_mcp_path", lambda: home / ".cursor" / "mcp.json")
    monkeypatch.setattr(
        install,
        "claude_desktop_config_path",
        lambda: home / "Claude" / "claude_desktop_config.json",
    )
    monkeypatch.setattr(install, "claude_code_memory_path", lambda: home / ".claude" / "CLAUDE.md")
    monkeypatch.setattr(install, "cursor_global_rules_dir", lambda: home / ".cursor" / "rules")
    return home


# ---------------------------------------------------------------- instruction


def test_instruction_mentions_the_tools_that_matter():
    text = inject_mod.render_instruction()
    for tool in ("get_reality_context", "list_open_commitments", "search_reality"):
        assert tool in text, f"{tool} missing from the instruction"


def test_instruction_forbids_interrogating_the_user():
    """The core promise: the agent must not ask for background it can look up."""
    text = inject_mod.render_instruction()
    assert "不要反问" in text


def test_instruction_is_small_and_deliberately_static():
    """It rides in every request, and it must never contain memories.

    A snapshot here would go stale and need re-running, which is exactly the
    friction `ss setup` exists to remove.
    """
    text = inject_mod.render_instruction()
    assert len(text) < 1200, "the always-applied rule should stay small"
    for marker in ("进行中的承诺", "因果脉络", "现实上下文（"):
        assert marker not in text, "instruction must not embed context content"


def test_instruction_is_wrapped_in_markers():
    text = inject_mod.render_instruction()
    assert text.startswith(inject_mod.BEGIN)
    assert text.rstrip().endswith(inject_mod.END)


def test_instruction_is_idempotent(tmp_path):
    path = tmp_path / "rules" / "shadowscribe.mdc"
    block = inject_mod.render_instruction()
    assert inject_mod.inject(path, block, frontmatter="---\nalwaysApply: true\n---\n") == "created"
    assert inject_mod.inject(path, block) == "unchanged"
    assert path.read_text(encoding="utf-8").count(inject_mod.BEGIN) == 1


# --------------------------------------------------------------- mcp merging


def test_setup_preserves_existing_mcp_servers(sandbox):
    """The user's mcp.json already holds other servers — do not eat them."""
    mcp = sandbox / ".cursor" / "mcp.json"
    mcp.write_text(
        json.dumps(
            {"mcpServers": {"taskmaster-ai": {"command": "npx", "args": ["-y", "task-master-ai"]}}}
        ),
        encoding="utf-8",
    )

    report = install.setup(project_root=sandbox / "proj", server_command=["ss", "mcp"])

    data = json.loads(mcp.read_text(encoding="utf-8"))
    assert "taskmaster-ai" in data["mcpServers"], "an unrelated server was destroyed"
    assert data["mcpServers"]["shadowscribe"] == {"command": "ss", "args": ["mcp"]}
    assert not report.failures, [a.detail for a in report.failures]


def test_setup_backs_up_before_writing(sandbox):
    mcp = sandbox / ".cursor" / "mcp.json"
    original = {"mcpServers": {"other": {"command": "x"}}}
    mcp.write_text(json.dumps(original), encoding="utf-8")

    install.setup(project_root=sandbox / "proj", server_command=["ss", "mcp"])

    backup = mcp.with_suffix(mcp.suffix + ".bak")
    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_setup_reports_invalid_json_instead_of_clobbering(sandbox):
    """A JSONC file with comments is common; refuse rather than destroy it."""
    mcp = sandbox / ".cursor" / "mcp.json"
    mcp.write_text('{ // editor config\n  "mcpServers": {}\n}', encoding="utf-8")

    report = install.setup(project_root=sandbox / "proj", server_command=["ss", "mcp"])

    assert any(not a.ok and "not valid JSON" in a.detail for a in report.actions)
    assert "// editor config" in mcp.read_text(encoding="utf-8"), "file was modified anyway"


def test_setup_is_idempotent(sandbox):
    root = sandbox / "proj"
    install.setup(project_root=root, server_command=["ss", "mcp"])
    before = (sandbox / ".cursor" / "mcp.json").read_text(encoding="utf-8")

    report = install.setup(project_root=root, server_command=["ss", "mcp"])

    assert (sandbox / ".cursor" / "mcp.json").read_text(encoding="utf-8") == before
    assert all(a.ok for a in report.actions)
    assert any("already registered" in a.detail for a in report.actions)


# ------------------------------------------------------------------- outputs


def test_setup_writes_the_static_rule_everywhere_it_can(sandbox):
    root = sandbox / "proj"
    install.setup(project_root=root, server_command=["ss", "mcp"])

    project_rule = root / ".cursor" / "rules" / "shadowscribe.mdc"
    assert project_rule.exists()
    assert "alwaysApply: true" in project_rule.read_text(encoding="utf-8")
    assert inject_mod.BEGIN in project_rule.read_text(encoding="utf-8")

    assert (root / "AGENTS.md").exists()
    assert (sandbox / ".claude" / "CLAUDE.md").exists()
    assert (sandbox / ".cursor" / "rules" / "shadowscribe.mdc").exists()


def test_setup_can_skip_global_scope(sandbox):
    root = sandbox / "proj"
    install.setup(project_root=root, server_command=["ss", "mcp"], global_scope=False)

    assert (root / ".cursor" / "rules" / "shadowscribe.mdc").exists()
    assert not (sandbox / ".claude" / "CLAUDE.md").exists()


def test_setup_does_not_touch_unrelated_project_files(sandbox):
    root = sandbox / "proj"
    root.mkdir(parents=True)
    (root / "AGENTS.md").write_text("# 我的规则\n\n不要删我。\n", encoding="utf-8")

    install.setup(project_root=root, server_command=["ss", "mcp"])

    content = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "不要删我。" in content
    assert inject_mod.BEGIN in content


def test_user_rules_hint_is_paste_ready(sandbox):
    report = install.setup(project_root=sandbox / "proj", server_command=["ss", "mcp"])
    assert "get_reality_context" in report.user_rules_hint
    # no managed markers: the user pastes this into a UI field, not a file
    assert inject_mod.BEGIN not in report.user_rules_hint


# ------------------------------------------------------------ server command


def test_resolve_server_command_is_absolute():
    """Editors launched from a GUI have a different PATH than the installing shell."""
    command = install.resolve_server_command()
    assert command[-1] == "mcp"
    assert len(command) >= 2
    if len(command) == 2:
        assert Path(command[0]).is_absolute(), command
    else:  # python -m fallback
        assert Path(command[0]).is_absolute()
        assert command[1:3] == ["-m", "shadowscribe_client.cli"]


def test_setup_writes_the_resolved_command(sandbox):
    install.setup(project_root=sandbox / "proj")
    data = json.loads((sandbox / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    entry = data["mcpServers"]["shadowscribe"]
    assert entry["args"] == ["mcp"]
    assert Path(entry["command"]).is_absolute() or entry["command"] == "ss"
