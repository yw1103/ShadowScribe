"""MCP surface — served **from the server**, not from the user's laptop.

The desktop should not need a Python package to talk to its own memory. Cursor
(and Claude, and anything else speaking MCP) can connect to a remote endpoint
directly:

    { "mcpServers": { "shadowscribe": {
        "url": "http://<host>:18080/mcp",
        "headers": { "Authorization": "Bearer <SS_TOKEN>" } } } }

An earlier design shipped a stdio MCP server inside the client package, which
proxied back to this server over HTTP. That was a redundant hop — and it forced a
`pip install` onto the machine that should only need a URL. The client is now CLI
only; everything MCP lives here.

Two tool families are exposed together because they answer different questions and
the agent should not have to know which store to ask:

* **sensory layer** (ShadowScribe's own SQLite) — what was said, what was promised,
  what happened today. ``causal-memory`` has none of this: commitments, transcripts
  and daily episodes never enter its graph.
* **memory layer** (``causal-memory``, in-process) — causal retrieval, backward
  tracing, forward simulation.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import datetime, timezone

from . import service
from .config import settings as default_settings
from .db import Session, get_engine, init_db
from .memory import get_backend

log = logging.getLogger(__name__)


def _server_class():
    """``mcp`` 2.x renamed FastMCP to MCPServer; support both."""
    try:
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP

        return FastMCP
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "the MCP surface needs the 'mcp' package: pip install 'shadowscribe[mcp]'"
        ) from exc


INSTRUCTIONS = (
    "影书 ShadowScribe：主人现实世界（线下对话、会议、电话）的因果记忆。\n"
    "当用户给出简短、缺少背景的指令（如「写测试用例」「草拟回复邮件」"
    "「把刚才聊的 bug 记下来」）时，**先调用 get_reality_context**，"
    "它包含主人最近现实中达成的决策、做出的承诺和因果关系。\n"
    "不要反问用户「你指的是什么」——答案大概率已经在上下文里。"
)


def build_mcp_server(settings=None):
    """Construct the MCP application. Importable without ``mcp`` installed."""
    Server = _server_class()
    s = settings or default_settings
    init_db()

    mcp = Server("shadowscribe", instructions=INSTRUCTIONS)
    backend = None

    def memory():
        """Lazily open the memory backend — it is expensive and optional."""
        nonlocal backend
        if backend is None:
            backend = get_backend(s)
        return backend

    # ------------------------------------------------------- sensory layer

    @mcp.tool()
    def get_reality_context(hours: int = 24, max_tokens: int = 1800) -> str:
        """获取主人最近现实世界的完整上下文卡片（决策 / 承诺 / 因果 / 涉及的人）。

        这是最重要的工具。用户给出简短指令、而你缺少背景时，先调用它。
        返回 markdown：进行中的承诺、关键决策、因果脉络、涉及的人与项目、原话锚点。
        把它当作既定事实使用，不要复述给用户听。

        Args:
            hours: 回溯多少小时，默认 24。要更长的历史可以给 168（一周）。
            max_tokens: 上下文预算，默认 1800；要完整上下文可提到 4000。
        """
        try:
            return service.build_brief(
                service.BriefOptions(hours=max(1, min(hours, 24 * 30)), max_tokens=max_tokens)
            )
        except Exception as exc:  # noqa: BLE001 - a tool must never kill the session
            log.exception("get_reality_context failed")
            return f"[shadowscribe] 读取上下文失败：{type(exc).__name__}: {exc}"

    @mcp.tool()
    def list_open_commitments(status: str = "open") -> str:
        """列出主人在现实中承诺过、但尚未完成的待办事项。

        在规划工作、写周报、判断优先级，或用户问「我还欠什么」时调用。
        每条包含承诺内容、对谁承诺、截止时间和支撑原话。

        Args:
            status: open（默认）/ done / cancelled / all。
        """
        if status not in {"open", "done", "cancelled", "all"}:
            status = "open"
        try:
            rows = service.list_commitments(status=status, limit=100)
        except Exception as exc:  # noqa: BLE001
            log.exception("list_open_commitments failed")
            return f"[shadowscribe] 读取承诺失败：{type(exc).__name__}: {exc}"

        if not rows:
            return f"没有 {status} 状态的承诺。"
        out = [f"{len(rows)} 项 {status} 承诺：", ""]
        for row in rows:
            due = row.due_at.strftime("%Y-%m-%d") if row.due_at else (row.due_text or "未指定")
            to = f" → {row.counterparty}" if row.counterparty else ""
            owner = "" if row.owner in ("我", "owner", "") else f"[{row.owner}] "
            out.append(f"- {owner}{row.what}{to}（截止 {due}，置信 {row.confidence:.0%}）")
            if row.evidence:
                out.append(f"  原话：{row.evidence[:140]}")
        return "\n".join(out)

    @mcp.tool()
    def search_reality(query: str, limit: int = 10) -> str:
        """在主人的现实记忆与原始转写中检索。

        当用户提到某个具体的人、项目或事件，而你需要确认「之前到底怎么说的」时调用。
        返回两段：结构化因果记忆命中 + 带时间戳的原始对话片段。

        Args:
            query: 关键词或自然语言问题，如「登录页」「Safari 白屏」「老王的要求」。
            limit: 每部分最多返回条数。
        """
        try:
            payload = service.semantic_search(query, limit=max(1, min(limit, 50)))
        except Exception as exc:  # noqa: BLE001
            log.exception("search_reality failed")
            return f"[shadowscribe] 检索失败：{type(exc).__name__}: {exc}"

        parts = [f"## 记忆库命中（{query}）", payload.get("memory") or "（无）"]
        transcripts = payload.get("transcripts") or []
        if transcripts:
            parts.append("\n## 原始转写片段")
            for row in transcripts:
                who = {"owner": "主人", "guest": "对方"}.get(row.get("speaker") or "", "未知")
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
        parsed = None
        if day:
            try:
                parsed = datetime.strptime(day.strip(), "%Y-%m-%d").date()
            except ValueError:
                return f"[shadowscribe] day 必须是 YYYY-MM-DD，收到 {day!r}"
        try:
            items = service.timeline(parsed)
        except Exception as exc:  # noqa: BLE001
            log.exception("get_timeline failed")
            return f"[shadowscribe] 读取时间轴失败：{type(exc).__name__}: {exc}"

        target = parsed or datetime.now(timezone.utc).date()
        if not items:
            return f"{target.isoformat()}：没有记录。"
        out = [f"# {target.isoformat()}", ""]
        for item in items:
            out.append(f"## {item['time']} {item['title']}")
            if item.get("summary"):
                out.append(item["summary"])
            if item.get("topics"):
                out.append(f"话题：{'、'.join(item['topics'])}")
            out.append("")
        return "\n".join(out)

    # -------------------------------------------------------- memory layer

    @mcp.tool()
    def search_memory(query: str, limit: int = 10) -> str:
        """在因果记忆图谱里检索（含海马体扩散激活）。比 search_reality 更偏向"决策→结果"。

        Args:
            query: 自然语言问题或关键词。
            limit: 最多返回条数。
        """
        try:
            return memory().search(query, limit=max(1, min(limit, 50)), detail_level="l2")
        except Exception as exc:  # noqa: BLE001
            return f"[shadowscribe] 记忆检索失败：{type(exc).__name__}: {exc}"

    @mcp.tool()
    def causal_directory(limit: int = 20) -> str:
        """列出最近的「决策 → 结果」边，紧凑的 L0 指针列表。

        想知道"我最近做过哪些决定、结果如何"时用，比拉整张上下文卡片便宜。
        """
        try:
            return memory().directory(limit=max(1, min(limit, 100)))
        except Exception as exc:  # noqa: BLE001
            return f"[shadowscribe] 读取因果目录失败：{type(exc).__name__}: {exc}"

    # -------------------------------------------------------------- wiring

    mcp._shadowscribe_backend = memory  # type: ignore[attr-defined]
    return mcp


# ------------------------------------------------------------- ASGI mounting


class BearerAuth:
    """Require ``Authorization: Bearer <token>`` on the mounted MCP app.

    The MCP SDK's own auth is OAuth-shaped; a single static token is what this
    deployment actually has, and the endpoint sits on the same public port as the
    ingest API, so it must not be left open.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.token:
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        provided = headers.get("authorization", "")
        if not provided.lower().startswith("bearer "):
            provided = headers.get("x-ss-token", "")
        else:
            provided = provided[7:].strip()

        if provided != self.token:
            body = json.dumps({"error": "invalid or missing bearer token"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b'Bearer realm="shadowscribe"'),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


class _McpPathRewrite:
    """Accept ``/mcp`` and ``/mcp/...`` without a redirect.

    ``app.mount("/mcp", ...)`` does not work here: Starlette compiles a mount to
    ``^/mcp/(?P<path>.*)$``, which never matches the bare ``/mcp`` an editor puts
    in its config. The parent router then answers 307 → ``/mcp/``, and whether an
    MCP client follows that on a POST carrying a JSON-RPC body is anybody's guess.
    Rewriting the path internally sidesteps the question entirely.
    """

    def __init__(self, app, prefix: str = "/mcp") -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        rest = path[len(self.prefix) :] if path.startswith(self.prefix) else path
        if not rest.startswith("/"):
            rest = "/" + rest
        scope = dict(scope)
        scope["path"] = rest
        scope["root_path"] = scope.get("root_path", "") + self.prefix
        await self.app(scope, receive, send)


def mount_mcp(app, settings=None, prefix: str = "/mcp") -> bool:
    """Attach the MCP endpoint. Returns False when ``mcp`` is absent."""
    s = settings or default_settings
    try:
        mcp = build_mcp_server(s)
    except RuntimeError as exc:
        log.warning("MCP surface disabled: %s", exc)
        return False

    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.routing import Route

    # Reached by public IP through a tunnel, so the SDK's localhost-oriented DNS
    # rebinding guard has to be off; the bearer token is the actual gate.
    transport = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    inner = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        transport_security=transport,
        max_request_body_size=8 * 1024 * 1024,
    )

    handler: object = _McpPathRewrite(inner, prefix)
    auth_note = "no auth"
    if s.mcp_require_token and s.effective_token:
        # Off by default. This is a single-user tool on the owner's own box, and a
        # token in an editor config buys nothing during an MVP. Set
        # SS_MCP_REQUIRE_TOKEN=true once the endpoint stops being private.
        handler = BearerAuth(handler, s.effective_token)
        auth_note = "bearer auth"

    # A Route whose endpoint is not a plain function is used as a raw ASGI app.
    methods = ["GET", "POST", "DELETE", "OPTIONS"]
    app.router.routes.append(Route(prefix, endpoint=handler, methods=methods))
    app.router.routes.append(Route(f"{prefix}/{{rest:path}}", endpoint=handler, methods=methods))

    # Starlette does not run a child app's lifespan for us. Without this the
    # streamable-HTTP session manager never starts and every request 500s.
    previous = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def combined(_app):
        async with previous(_app), inner.router.lifespan_context(inner):
            yield

    app.router.lifespan_context = combined
    log.info("MCP endpoint mounted at %s (streamable HTTP, %s)", prefix, auth_note)
    return True


__all__ = ["BearerAuth", "build_mcp_server", "mount_mcp", "Session", "get_engine"]
