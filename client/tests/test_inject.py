"""Client tests.

The injection logic is the only part of the client with real state-mutation risk:
it edits files the user already owns. Idempotency is therefore the core assertion.
"""

from __future__ import annotations

import json

import pytest

from shadowscribe_client.config import ClientConfig
from shadowscribe_client.inject import (
    BEGIN,
    END,
    TARGETS,
    detect_targets,
    inject,
    remove,
    render_block,
)

BRIEF = "# 影书 · 现实上下文\n\n- [ ] 提交修复方案 → 老王\n"


# --------------------------------------------------------------------- render


def test_render_block_is_bounded_by_markers():
    block = render_block(BRIEF, target=TARGETS["claude"])
    assert block.startswith(BEGIN)
    assert block.rstrip().endswith(END)
    assert "提交修复方案" in block


def test_render_block_warns_the_model_this_is_not_user_written():
    """The preamble is what stops the agent asking the user to re-explain."""
    block = render_block(BRIEF, target=TARGETS["claude"])
    assert "不是用户手写的" in block


# -------------------------------------------------------------------- inject


def test_inject_creates_file_with_frontmatter(tmp_path):
    path = tmp_path / "rules" / "shadowscribe.mdc"
    outcome = inject(
        path,
        render_block(BRIEF, target=TARGETS["cursor"]),
        frontmatter="---\nalwaysApply: true\n---\n",
    )
    assert outcome == "created"
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---\nalwaysApply: true\n---\n")
    assert "提交修复方案" in content


def test_inject_is_idempotent(tmp_path):
    """Running `ss inject` twice must not append a second copy."""
    path = tmp_path / "CLAUDE.md"
    block = render_block(BRIEF, target=TARGETS["claude"])

    assert inject(path, block) == "created"
    assert inject(path, block) == "unchanged"
    assert path.read_text(encoding="utf-8").count(BEGIN) == 1


def test_inject_updates_in_place(tmp_path):
    path = tmp_path / "CLAUDE.md"
    inject(path, render_block(BRIEF, target=TARGETS["claude"]))

    newer = render_block(BRIEF + "\n- [ ] 新的承诺\n", target=TARGETS["claude"])
    assert inject(path, newer) == "updated"

    content = path.read_text(encoding="utf-8")
    assert content.count(BEGIN) == 1
    assert "新的承诺" in content
    assert "提交修复方案" in content  # the older line survives inside the same block


def test_inject_preserves_surrounding_content(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text("# 我的项目\n\n一些手写规则。\n", encoding="utf-8")

    inject(path, render_block(BRIEF, target=TARGETS["claude"]))
    content = path.read_text(encoding="utf-8")

    assert "# 我的项目" in content
    assert "一些手写规则。" in content
    assert "提交修复方案" in content


def test_remove_strips_only_the_managed_block(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text("# 我的项目\n", encoding="utf-8")
    inject(path, render_block(BRIEF, target=TARGETS["claude"]))

    assert remove(path) == "removed"
    content = path.read_text(encoding="utf-8")
    assert "# 我的项目" in content
    assert BEGIN not in content
    assert "提交修复方案" not in content
    assert remove(path) == "absent"


def test_inject_handles_file_without_trailing_newline(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text("no trailing newline", encoding="utf-8")
    inject(path, render_block(BRIEF, target=TARGETS["agents"]))

    content = path.read_text(encoding="utf-8")
    assert content.startswith("no trailing newline")
    assert BEGIN in content


# ------------------------------------------------------------------- targets


def test_detect_targets_finds_editor_markers(tmp_path):
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".github").mkdir()
    (tmp_path / "CLAUDE.md").write_text("", encoding="utf-8")

    keys = {target.key for target, _ in detect_targets(tmp_path)}
    assert keys == {"cursor", "copilot", "claude"}


def test_detect_targets_empty_repo(tmp_path):
    assert detect_targets(tmp_path) == []


def test_every_target_has_a_distinct_path():
    paths = [t.path for t in TARGETS.values()]
    assert len(paths) == len(set(paths))


# -------------------------------------------------------------------- config


@pytest.fixture()
def no_config_env(monkeypatch):
    """Clear the env vars that outrank the config file.

    Environment beats file in :meth:`ClientConfig.load`, so a developer with
    ``SS_ENDPOINT`` / ``SS_TOKEN`` exported — or a CI job that sets them — would
    see the file-based expectations shadowed and these tests fail for reasons
    unrelated to the behaviour under test.
    """
    for name in ("SS_ENDPOINT", "SS_TOKEN", "SS_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_config_precedence_flags_beat_env(tmp_path, no_config_env):
    no_config_env.setenv("SS_CONFIG", str(tmp_path / "cfg.json"))
    no_config_env.setenv("SS_ENDPOINT", "http://from-env")
    no_config_env.setenv("SS_TOKEN", "env-token")
    no_config_env.chdir(tmp_path)

    assert ClientConfig.load().endpoint == "http://from-env"
    assert ClientConfig.load(endpoint="http://from-flag").endpoint == "http://from-flag"


def test_config_project_file_overrides_home(tmp_path, no_config_env):
    no_config_env.setenv("SS_CONFIG", str(tmp_path / "home.json"))
    no_config_env.chdir(tmp_path)

    (tmp_path / "home.json").write_text(
        json.dumps({"endpoint": "http://home", "token": "home-token"}), encoding="utf-8"
    )
    (tmp_path / ".shadowscribe.json").write_text(
        json.dumps({"endpoint": "http://project"}), encoding="utf-8"
    )

    cfg = ClientConfig.load()
    assert cfg.endpoint == "http://project"
    assert cfg.token == "home-token"  # unspecified keys still fall through


def test_config_save_is_round_trippable(tmp_path, no_config_env):
    target = tmp_path / "nested" / "config.json"
    no_config_env.setenv("SS_CONFIG", str(target))
    no_config_env.chdir(tmp_path)

    cfg = ClientConfig.load()
    cfg.endpoint = "http://saved"
    cfg.token = "saved-token"
    cfg.save()

    assert target.exists()
    reloaded = ClientConfig.load()
    assert reloaded.endpoint == "http://saved"
    assert reloaded.token == "saved-token"


def test_config_ignores_corrupt_file(tmp_path, no_config_env):
    bad = tmp_path / "config.json"
    bad.write_text("{not json", encoding="utf-8")
    no_config_env.setenv("SS_CONFIG", str(bad))
    no_config_env.chdir(tmp_path)

    # must fall back to defaults rather than raising on every `ss` invocation
    assert ClientConfig.load().endpoint.startswith("http")


@pytest.mark.parametrize("key", sorted(TARGETS))
def test_known_targets_resolve(key):
    from shadowscribe_client.inject import resolve_targets

    pairs = resolve_targets([key])
    assert pairs[0][0].key == key


def test_unknown_target_raises():
    from shadowscribe_client.inject import resolve_targets

    with pytest.raises(KeyError):
        resolve_targets(["not-an-editor"])
