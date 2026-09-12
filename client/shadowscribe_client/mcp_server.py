"""MCP server — the desktop agent's silent pipe into the causal memory.

Run it over stdio and register it with Cursor / Claude Desktop / Claude Code:

    {
      "mcpServers": {
        "shadowscribe": { "command": "ss", "args": ["mcp"] }
      }
    }

The tool descriptions are deliberately prescriptive. An agent will not call a
memory tool it does not understand the *purpose* of, so each docstring explains
when to reach for it — that is what turns an available tool into automatic
context inheritance (原则三).

Nothing here writes to the user's environment and nothing ever speaks, notifies
or interrupts (原则一).
"""

from __future__ import annotations

import logging
import sys

from .api import ShadowScribeClient, ShadowScribeError
from .config import ClientConfig

log = logging.getLogger(__name__)

# stdout is the MCP wire — logs must never touch it.
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)


def _client() -> ShadowScribeClient:
    return ShadowScribeClient(ClientConfig.load())


def _friendly(exc: Exception) -> str:
    return (
        f"[shadowscribe] 无法读取现实上下文：{exc}\n"
        "请确认服务器可达且 SS_ENDPOINT / SS_TOKEN 正确（可在终端运行 `ss status` 检查）。"
        "在拿到上下文之前，请正常向用户询问必要背景。"
    )


def build_server():
    """Construct the FastMCP app. Imported lazily so ``ss`` works without ``mcp``."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - guarded by pyproject extra
        raise SystemExit(
            "the MCP server needs the 'mcp' package:  pip install 'shadowscribe-client[mcp]'"
        ) from exc

    mcp = FastMCP(
        "shadowscribe",
        instructions=(
            "影书 ShadowScribe：主人现实世界（线下对话、会议、电话）的因果记忆。\n"
            "当用户给出一个简短、缺少背景的指令（如「写测试用例」「草拟回复邮件」"
            "「把刚才聊的 bug 记下来」）时，**先调用 get_reality_context**，"
            "它包含主人最近现实中达成的决策、做出的承诺和因果关系。\n"
            "不要反问用户「你指的是什么」——答案大概率已经在上下文里。"
        ),
    )

    @mcp.tool()
    def get_reality_context(hours: int = 24, max_tokens: int = 1800) -> str:
        """获取主人最近现实世界的完整上下文卡片（决策 / 承诺 / 因果 / 涉及的人）。

        这是最重要的工具。在用户给出简短指令、而你缺少背景时，先调用它。
        返回 markdown：包含进行中的承诺、关键决策、因果脉络、涉及的人与项目、
        以及原话锚点。把它当作既定事实使用，不要复述给用户听。

        Args:
            hours: 回溯多少小时，默认 24。
            max_tokens: 上下文预算，默认 1800；要完整上下文可提到 4000。
        """
        try:
            with _client() as client:
                return client.brief(hours=hours, max_tokens=max_tokens)
        except (ShadowScribeError, Exception) as exc:  # noqa: BLE001
            return _friendly(exc)

    @mcp.tool()
    def list_open_commitments(status: str = "open") -> str:
        """列出主人在现实中承诺过、但尚未完成的待办事项。

        在规划工作、写周报、判断优先级、或用户问「我还欠什么」时调用。
        每条包含承诺内容、对谁承诺、截止时间和支撑原话。

        Args:
            status: open（默认）/ done / cancelled / all。
        """
        try:
            with _client() as client:
                payload = client.commitments(status=status)
        except Exception as exc:  # noqa: BLE001
            return _friendly(exc)

        rows = payload.get("commitments") or []
        if not rows:
            return f"没有 {status} 状态的承诺。"
        lines = [f"{len(rows)} 项 {status} 承诺：", ""]
        for row in rows:
            due = row.get("due_at") or row.get("due_text") or "未指定"
            to = f" → {row['counterparty']}" if row.get("counterparty") else ""
            lines.append(f"- {row['what']}{to}（截止 {due}，置信 {row.get('confidence', 0):.0%}）")
            if row.get("evidence"):
                lines.append(f"  原话：{row['evidence'][:120]}")
        return "\n".join(lines)

    @mcp.tool()
    def search_reality(query: str, limit: int = 10) -> str:
        """在主人的现实记忆与原始转写中检索。

        当用户提到某个具体的人、项目、事件，而你需要确认「之前到底怎么说的」时调用。
        返回两段：结构化因果记忆命中 + 带时间戳的原始对话片段。

        Args:
            query: 关键词或自然语言问题，如「登录页」「Safari 白屏」「老王的要求」。
            limit: 每部分最多返回条数。
        """
        try:
            with _client() as client:
                payload = client.search(query, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return _friendly(exc)

        parts = [f"## 记忆库命中（{query}）", payload.get("memory") or "（无）"]
        transcripts = payload.get("transcripts") or []
        if transcripts:
            parts.append("\n## 原始转写片段")
            for row in transcripts:
                who = {"owner": "我", "guest": "对方"}.get(row.get("speaker") or "", "未知")
                ms = row.get("start_ms", 0)
                parts.append(f"- [{ms // 60000:02d}:{(ms // 1000) % 60:02d}] {who}: {row['text']}")
        return "\n".join(parts)

    @mcp.tool()
    def get_timeline(day: str = "") -> str:
        """查看某一天主人现实中发生了什么（按时间顺序的片段标题与摘要）。

        适合「今天都干了什么」「昨天下午聊了什么」这类问题。

        Args:
            day: YYYY-MM-DD，留空表示今天。
        """
        try:
            with _client() as client:
                payload = client.timeline(day or None)
        except Exception as exc:  # noqa: BLE001
            return _friendly(exc)

        items = payload.get("items") or []
        if not items:
            return f"{payload.get('day')}：没有记录。"
        lines = [f"# {payload['day']}", ""]
        for item in items:
            lines.append(f"## {item['time']} {item['title']}")
            if item.get("summary"):
                lines.append(item["summary"])
            if item.get("topics"):
                lines.append(f"话题：{'、'.join(item['topics'])}")
            lines.append("")
        return "\n".join(lines)

    @mcp.tool()
    def pending_work_summary() -> str:
        """一次性拉取「上下文卡片 + 全部未完成承诺」，适合新会话开场时调用。"""
        try:
            with _client() as client:
                return client.brief(hours=48, max_tokens=2500)
        except Exception as exc:  # noqa: BLE001
            return _friendly(exc)

    @mcp.resource("shadowscribe://brief")
    def brief_resource() -> str:
        """今天/最近的现实上下文卡片（resources 形式，可被客户端自动附加）。"""
        try:
            with _client() as client:
                return client.brief()
        except Exception as exc:  # noqa: BLE001
            return _friendly(exc)

    @mcp.resource("shadowscribe://commitments")
    def commitments_resource() -> str:
        """当前未完成的现实承诺清单。"""
        return list_open_commitments("open")

    return mcp


def run() -> None:
    build_server().run()


if __name__ == "__main__":
    run()
