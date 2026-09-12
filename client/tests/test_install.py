"""Tests for `ss setup` — pointing the editor at the server's MCP endpoint.

The highest-risk behaviour is merging into config files the user already owns:
their ``mcp.json`` holds other servers, often with credentials inline. A bug that
overwrites it destroys something this tool was never given permission to touch, so
that is tested first and hardest.

Every path is monkeypatched — these tests must never write to a real home
directory.
"""

from __future__ import annotations

import json

import pytest

from shadowscribe_client import inject as inject_mod
from shadowscribe_client import install

ENDPOINT = "http://example.test:18080"
TOKEN = "test-token-123"


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


def run_setup(sandbox, **kwargs):
    kwargs.setdefault("endpoint", ENDPOINT)
    kwargs.setdefault("project_root", sandbox / "proj")
    return install.setup(**kwargs)


# --------------------------------------------------------------- url shaping


def test_mcp_url_appends_the_mount_point():
    assert install.mcp_url("http://host:18080") == "http://host:18080/mcp"
    assert install.mcp_url("http://host:18080/") == "http://host:18080/mcp"


def test_setup_writes_a_remote_url_not_a_command(sandbox):
    """The server hosts MCP; the laptop must not need a stdio command.

    An earlier design registered `ss mcp` as a local command which then proxied
    back to this same server over HTTP. That forced a pip install onto a machine
    which should only need a URL.
    """
    run_setup(sandbox)
    data = json.loads((sandbox / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    entry = data["mcpServers"]["shadowscribe"]

    assert "url" in entry, entry
    assert entry["url"] == f"{ENDPOINT}/mcp"
    assert "command" not in entry, "a local stdio command was registered"
    assert "headers" not in entry, "auth header written for an open endpoint"


def test_setup_writes_no_auth_header_for_the_mvp(sandbox):
    """The endpoint is open on the owner's own box; a header buys nothing yet."""
    run_setup(sandbox)
    entry = json.loads((sandbox / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    entry = entry["mcpServers"]["shadowscribe"]
    assert entry == {"url": f"{ENDPOINT}/mcp"}, entry


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

    report = run_setup(sandbox)

    data = json.loads(mcp.read_text(encoding="utf-8"))
    assert "taskmaster-ai" in data["mcpServers"], "an unrelated server was destroyed"
    assert data["mcpServers"]["shadowscribe"]["url"] == f"{ENDPOINT}/mcp"
    assert not report.failures, [a.detail for a in report.failures]


def test_setup_backs_up_before_writing(sandbox):
    mcp = sandbox / ".cursor" / "mcp.json"
    original = {"mcpServers": {"other": {"command": "x"}}}
    mcp.write_text(json.dumps(original), encoding="utf-8")

    run_setup(sandbox)

    backup = mcp.with_suffix(mcp.suffix + ".bak")
    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_setup_reports_invalid_json_instead_of_clobbering(sandbox):
    """A JSONC file with comments is common; refuse rather than destroy it."""
    mcp = sandbox / ".cursor" / "mcp.json"
    mcp.write_text('{ // editor config\n  "mcpServers": {}\n}', encoding="utf-8")

    report = run_setup(sandbox)

    assert any(not a.ok and "not valid JSON" in a.detail for a in report.actions)
    assert "// editor config" in mcp.read_text(encoding="utf-8"), "file was modified anyway"


def test_setup_is_idempotent(sandbox):
    run_setup(sandbox)
    before = (sandbox / ".cursor" / "mcp.json").read_text(encoding="utf-8")

    report = run_setup(sandbox)

    assert (sandbox / ".cursor" / "mcp.json").read_text(encoding="utf-8") == before
    assert all(a.ok for a in report.actions)
    assert any("already registered" in a.detail for a in report.actions)


def test_setup_updates_when_the_endpoint_moves(sandbox):
    """Moving to a real server must not require hand-editing mcp.json."""
    run_setup(sandbox)
    run_setup(sandbox, endpoint="http://new-host:9000")

    entry = json.loads((sandbox / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert entry["mcpServers"]["shadowscribe"]["url"] == "http://new-host:9000/mcp"


# ------------------------------------------------------------------- outputs


def test_setup_writes_the_static_rule_everywhere_it_can(sandbox):
    root = sandbox / "proj"
    run_setup(sandbox, project_root=root)

    project_rule = root / ".cursor" / "rules" / "shadowscribe.mdc"
    assert project_rule.exists()
    content = project_rule.read_text(encoding="utf-8")
    assert "alwaysApply: true" in content
    assert inject_mod.BEGIN in content

    assert (root / "AGENTS.md").exists()
    assert (sandbox / ".claude" / "CLAUDE.md").exists()
    assert (sandbox / ".cursor" / "rules" / "shadowscribe.mdc").exists()


def test_setup_can_skip_global_scope(sandbox):
    run_setup(sandbox, global_scope=False)
    assert (sandbox / "proj" / ".cursor" / "rules" / "shadowscribe.mdc").exists()
    assert not (sandbox / ".claude" / "CLAUDE.md").exists()


def test_setup_does_not_touch_unrelated_project_files(sandbox):
    root = sandbox / "proj"
    root.mkdir(parents=True)
    (root / "AGENTS.md").write_text("# 我的规则\n\n不要删我。\n", encoding="utf-8")

    run_setup(sandbox, project_root=root)

    content = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "不要删我。" in content
    assert inject_mod.BEGIN in content


def test_user_rules_hint_is_paste_ready(sandbox):
    report = run_setup(sandbox)
    assert "get_reality_context" in report.user_rules_hint
    # no managed markers: the user pastes this into a UI field, not a file
    assert inject_mod.BEGIN not in report.user_rules_hint


def test_report_exposes_the_url_it_wrote(sandbox):
    report = run_setup(sandbox)
    assert report.mcp_url == f"{ENDPOINT}/mcp"


# ---------------------------------------------------------------- instruction


def test_instruction_mentions_the_tools_that_matter():
    text = inject_mod.render_instruction()
    for tool in ("get_reality_context", "list_open_commitments", "search_reality"):
        assert tool in text, f"{tool} missing from the instruction"


def test_instruction_forbids_interrogating_the_user():
    """The core promise: the agent must not ask for background it can look up."""
    assert "不要反问" in inject_mod.render_instruction()


def test_instruction_is_small_and_deliberately_static():
    """It rides in every request, and it must never contain memories.

    A snapshot here would go stale and need re-running, which is exactly the
    friction `ss setup` exists to remove.
    """
    text = inject_mod.render_instruction()
    assert len(text) < 1200, "the always-applied rule should stay small"
    for marker in ("进行中的承诺", "因果脉络"):
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
