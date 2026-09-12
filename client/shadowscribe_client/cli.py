"""``ss`` — the desktop command line.

The whole point: the user sits down, types four characters, and the AI's context
window already contains today's reality.

    ss login --endpoint http://host:18080 --token ...
    ss brief                     # print the context card
    ss inject --auto             # write it into Cursor / CLAUDE.md / AGENTS.md
    ss commitments               # what did I promise?
    ss search 登录页
    ss mcp                       # stdio MCP server for Cursor / Claude Desktop
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import inject as inject_mod
from .api import ShadowScribeClient, ShadowScribeError
from .config import ClientConfig

# --------------------------------------------------------------------- helpers


def _configure_stdio() -> None:
    """Make output survive a non-UTF-8 console.

    Windows consoles default to a legacy code page (GBK/cp936 on Chinese installs),
    and the context card contains CJK plus emoji. Without this, ``ss brief`` dies
    with ``UnicodeEncodeError`` on the first ⏳ — i.e. it fails on exactly the
    platform this tool is most likely to run on.

    Prefer UTF-8 (correct on Windows Terminal / PowerShell 7+). If that is not
    possible, at least degrade to replacement characters instead of a traceback.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            with contextlib.suppress(AttributeError, OSError, ValueError):
                stream.reconfigure(errors="replace")


def _client(args) -> ShadowScribeClient:
    cfg = ClientConfig.load(
        endpoint=getattr(args, "endpoint", None), token=getattr(args, "token", None)
    )
    return ShadowScribeClient(cfg)


def _copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard write; the platform's own tool is the least fragile."""
    candidates: list[list[str]] = []
    if sys.platform == "win32":
        candidates = [["clip"]]
    elif sys.platform == "darwin":
        candidates = [["pbcopy"]]
    else:
        candidates = [
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]

    for cmd in candidates:
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=10)
                return True
            except (subprocess.SubprocessError, OSError):
                continue
    return False


def _fail(message: str, code: int = 1) -> int:
    print(f"ss: {message}", file=sys.stderr)
    return code


# -------------------------------------------------------------------- commands


def cmd_login(args) -> int:
    cfg = ClientConfig.load()
    if args.endpoint:
        cfg.endpoint = args.endpoint
    if args.token:
        cfg.token = args.token
    if args.hours:
        cfg.default_hours = args.hours
    if args.max_tokens:
        cfg.default_max_tokens = args.max_tokens

    path = cfg.save()
    print(f"config written to {path}")

    client = ShadowScribeClient(cfg)
    try:
        info = client.health()
    except ShadowScribeError as exc:
        return _fail(f"saved, but the server did not answer: {exc}")
    finally:
        client.close()

    counts = info.get("recordings", {})
    print(
        f"connected — backend={info.get('backend')} · "
        f"{counts.get('done', 0)} processed / {counts.get('queued', 0)} queued recordings"
    )
    return 0


def cmd_status(args) -> int:
    with _client(args) as client:
        info = client.health()
    counts = info.get("recordings", {})
    print(f"endpoint      : {client.cfg.endpoint}")
    print(f"memory backend: {info.get('backend')}")
    print(
        f"recordings    : {counts.get('recordings', 0)} total · "
        f"{counts.get('done', 0)} done · {counts.get('queued', 0)} queued · "
        f"{counts.get('failed', 0)} failed"
    )
    print(f"segments      : {counts.get('segments', 0)}")
    print(f"episodes      : {counts.get('episodes', 0)}")
    print(f"open promises : {counts.get('commitments_open', 0)}")
    memory = info.get("memory") or {}
    if memory.get("db_bytes"):
        print(f"memory size   : {memory['db_bytes'] / 1024:.0f} KiB")
    if memory.get("error"):
        print(f"memory error  : {memory['error']}")
    return 0


def cmd_brief(args) -> int:
    with _client(args) as client:
        card = client.brief(
            hours=args.hours,
            max_tokens=args.max_tokens,
            quotes=not args.no_quotes,
            entities=not args.no_entities,
        )
    if args.out:
        Path(args.out).write_text(card, encoding="utf-8")
        print(f"wrote {args.out} ({len(card)} chars)")
    else:
        print(card)
    if args.copy:
        print(
            "copied to clipboard"
            if _copy_to_clipboard(card)
            else "could not reach a clipboard tool",
            file=sys.stderr,
        )
    return 0


def cmd_commitments(args) -> int:
    with _client(args) as client:
        if args.done:
            result = client.set_commitment(args.done, "done")
            print(f"commitment {result['id']} → {result['status']}")
            return 0
        payload = client.commitments(status=args.status, limit=args.limit)

    rows = payload.get("commitments", [])
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print(f"no {args.status} commitments")
        return 0

    print(f"{len(rows)} {args.status} commitment(s):\n")
    for row in rows:
        due = row.get("due_at") or row.get("due_text") or "—"
        to = f" → {row['counterparty']}" if row.get("counterparty") else ""
        owner = f"[{row['owner']}] " if row.get("owner") not in ("我", "owner", None) else ""
        print(f"  {row['id'][:8]}  {owner}{row['what']}{to}")
        print(f"            due: {due}   conf: {row.get('confidence', 0):.0%}")
        if row.get("evidence"):
            print(f"            原话: {row['evidence'][:90]}")
    print("\nmark done:  ss commitments --done <id>")
    return 0


def cmd_search(args) -> int:
    with _client(args) as client:
        payload = client.search(args.query, limit=args.limit)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print("== 记忆库 ==")
    print(payload.get("memory") or "(no hits)")
    transcripts = payload.get("transcripts") or []
    if transcripts:
        print("\n== 原始转写 ==")
        for row in transcripts:
            who = {"owner": "我", "guest": "对方"}.get(row.get("speaker") or "", "未知")
            stamp = row["start_ms"] // 60000, (row["start_ms"] // 1000) % 60
            print(f"  [{stamp[0]:02d}:{stamp[1]:02d}] {who}: {row['text']}")
    return 0


def cmd_timeline(args) -> int:
    with _client(args) as client:
        payload = client.timeline(args.day)
    print(f"# {payload['day']} — {payload['count']} 个片段\n")
    for item in payload["items"]:
        flag = " _(未蒸馏)_" if item.get("degraded") else ""
        print(f"{item['time']}  {item['title']}{flag}")
        if item.get("summary"):
            print(f"        {item['summary'][:160]}")
        meta = []
        if item.get("topics"):
            meta.append("话题: " + "、".join(item["topics"][:5]))
        if item.get("causes"):
            meta.append(f"因果 {item['causes']}")
        if item.get("commitments"):
            meta.append(f"待办 {item['commitments']}")
        if meta:
            print("        " + " · ".join(meta))
        print()
    return 0


def cmd_upload(args) -> int:
    with _client(args) as client:
        result = client.upload(args.path, session_hint=args.hint)
    verb = "already known" if result.get("dedup") else "queued"
    print(f"{verb}: recording={result['recording_id']} job={result.get('job_id')}")
    return 0


def cmd_recordings(args) -> int:
    with _client(args) as client:
        payload = client.recordings(limit=args.limit, status=args.status)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for row in payload["recordings"]:
        size = (row.get("size_bytes") or 0) / 1024 / 1024
        dur = (row.get("duration_ms") or 0) / 1000
        print(
            f"{row['recorded_at'][:16]}  {row['id'][:8]}  {row['status']:<10} "
            f"{dur:7.1f}s {size:6.1f}MB  {row.get('session_hint') or ''}"
        )
        if row.get("error"):
            print(f"           error: {row['error'][:120]}")
    return 0


def cmd_inject(args) -> int:
    with _client(args) as client:
        card = client.brief(hours=args.hours, max_tokens=args.max_tokens)

    if args.auto:
        pairs = inject_mod.detect_targets()
        if not pairs:
            pairs = inject_mod.resolve_targets(["cursor", "claude"])
            print("no editor markers found; defaulting to Cursor + CLAUDE.md", file=sys.stderr)
    else:
        pairs = inject_mod.resolve_targets(args.targets)

    if args.remove:
        for target, path in pairs:
            print(f"  {inject_mod.remove(path):<9} {target.label:<34} {path}")
        return 0

    if args.dry_run:
        print(inject_mod.render_block(card, target=pairs[0][0]))
        return 0

    for target, path in pairs:
        block = inject_mod.render_block(card, target=target)
        outcome = inject_mod.inject(path, block, frontmatter=target.frontmatter)
        print(f"  {outcome:<9} {target.label:<34} {path}")
    print(f"\n上下文已注入（{len(card)} 字符）。下次打开编辑器新会话时，AI 会自动带着它。")
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import run

    run()
    return 0


def cmd_doctor(args) -> int:
    """One command that answers "is my setup actually working?".

    Walks the whole chain the user depends on — config, network, auth, memory,
    data freshness, editor targets, MCP availability — and prints an actionable
    line for anything that is broken, instead of making them guess which of six
    moving parts failed.
    """
    from . import inject as inject_mod

    cfg = ClientConfig.load(
        endpoint=getattr(args, "endpoint", None), token=getattr(args, "token", None)
    )
    rows: list[tuple[bool, str, str]] = []
    hints: list[str] = []

    if cfg.endpoint != ClientConfig().endpoint or cfg.token:
        rows.append((True, "config", f"{ClientConfig.home()} → {cfg.endpoint}"))
    else:
        rows.append((False, "config", "not configured"))
        hints.append("运行 `ss login --endpoint <服务端地址> --token <SS_TOKEN>` 保存连接信息")

    rows.append(
        (bool(cfg.token), "token", f"已设置（{len(cfg.token)} 字符）" if cfg.token else "未设置")
    )
    if not cfg.token:
        hints.append("服务端 .env 里的 SS_TOKEN；`docker compose exec api printenv SS_TOKEN` 可取")

    client = ShadowScribeClient(cfg)
    start = time.perf_counter()
    try:
        reachable = client.ping()
        elapsed_ms = (time.perf_counter() - start) * 1000
        rows.append((reachable, "网络可达", f"{cfg.endpoint}/healthz  {elapsed_ms:.0f} ms"))
        if not reachable:
            hints.append("服务端没起、端口没开、或内网穿透规则失效")
    except Exception as exc:  # noqa: BLE001
        rows.append((False, "网络可达", str(exc)[:80]))
        hints.append("检查内网穿透是否在线，或先用 `ssh -N -L 18080:127.0.0.1:18080 <host>` 建隧道")

    info: dict = {}
    try:
        info = client.health()
        counts = info.get("recordings", {})
        rows.append(
            (
                True,
                "鉴权",
                f"{counts.get('done', 0)} 段已处理 · {counts.get('queued', 0)} 排队 · "
                f"{counts.get('failed', 0)} 失败 · {counts.get('commitments_open', 0)} 项待办",
            )
        )
        if counts.get("failed"):
            hints.append("有失败的录音：`ss recordings --status failed` 看原因，可 `ss` 重传")
        if not counts.get("done"):
            hints.append("还没有处理完的录音 —— 先从手机上传一段，或 `ss upload <文件>`")
        backend = info.get("backend")
        rows.append((backend == "causal-memory", "记忆后端", str(backend)))
        if backend != "causal-memory":
            hints.append("causal-memory 未生效，检索能力降级为内置 SQLite")
    except ShadowScribeError as exc:
        rows.append((False, "鉴权", str(exc)[:90]))
        if "401" in str(exc):
            hints.append("token 不对：重新 `ss login --token <SS_TOKEN>`")
    except Exception as exc:  # noqa: BLE001
        rows.append((False, "鉴权", str(exc)[:90]))

    if info:
        try:
            card = client.brief(hours=cfg.default_hours, max_tokens=200)
            fresh = "没有已处理的录音" not in card and "没有记录" not in card
            rows.append((fresh, f"最近 {cfg.default_hours}h 上下文", "有内容" if fresh else "为空"))
            if not fresh:
                hints.append(
                    f"最近 {cfg.default_hours} 小时内没有可用上下文；"
                    "或调大窗口：`ss brief --hours 720`"
                )
        except Exception as exc:  # noqa: BLE001
            rows.append((False, "上下文卡片", str(exc)[:80]))

    try:
        client.commitments(status="open", limit=1)
        rows.append((True, "承诺接口", "正常"))
    except Exception as exc:  # noqa: BLE001
        rows.append((False, "承诺接口", str(exc)[:80]))

    client.close()

    # Editor targets: which rule files exist in the current directory.
    detected = inject_mod.detect_targets()
    names = ", ".join(t.label for t, _ in detected) or "无"
    rows.append((True, "本目录编辑器", names))
    if not detected:
        hints.append("当前目录没有编辑器标记文件；`ss inject --target cursor` 可显式指定")

    try:
        import mcp  # noqa: F401

        rows.append((True, "MCP 依赖", "已安装（`ss mcp` 可用）"))
    except ImportError:
        rows.append((False, "MCP 依赖", "未安装"))
        hints.append('想用 MCP：pip install "shadowscribe-client[mcp]"')

    width = max(len(name) for _, name, _ in rows)
    failures = 0
    for ok, name, detail in rows:
        if not ok:
            failures += 1
        print(f"[{'OK  ' if ok else 'MISS'}] {name:<{width}}  {detail}")

    print()
    if hints:
        print("建议：")
        for hint in hints:
            print(f"  · {hint}")
        print()
    print(f"{len(rows) - failures}/{len(rows)} 项通过")
    return 0 if failures == 0 else 1


# ---------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ss",
        description="影书 ShadowScribe — 让 AI 记住因果，而不是让你重复背景",
        epilog="docs: https://github.com/yw1103/ShadowScribe",
    )
    parser.add_argument("--endpoint", help="server base URL (overrides config)")
    parser.add_argument("--token", help="bearer token (overrides config)")
    parser.add_argument(
        "--json", action="store_true", help="machine-readable output where supported"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="save endpoint + token to ~/.shadowscribe/config.json")
    p.add_argument("--endpoint")
    p.add_argument("--token")
    p.add_argument("--hours", type=int, help="default brief window")
    p.add_argument("--max-tokens", type=int, help="default brief budget")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("status", help="server health and store counts")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("brief", help="print the reality context card")
    p.add_argument("--hours", type=int, default=None)
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--out", help="write to a file")
    p.add_argument("--copy", action="store_true", help="also copy to the clipboard")
    p.add_argument("--no-quotes", action="store_true")
    p.add_argument("--no-entities", action="store_true")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("commitments", help="what did I promise?")
    p.add_argument("--status", default="open", choices=["open", "done", "cancelled", "all"])
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--done", metavar="ID", help="mark a commitment as done")
    p.set_defaults(func=cmd_commitments)

    p = sub.add_parser("search", help="search memory + raw transcripts")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("timeline", help="what happened on a given day")
    p.add_argument("--day", help="YYYY-MM-DD (default: today)")
    p.set_defaults(func=cmd_timeline)

    p = sub.add_parser("upload", help="push an audio file to the server")
    p.add_argument("path")
    p.add_argument("--hint", help="session hint, e.g. '与老王在会议室'")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("recordings", help="list recent recordings and their status")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--status", default=None)
    p.set_defaults(func=cmd_recordings)

    p = sub.add_parser("inject", help="write the context card into editor rule files")
    p.add_argument(
        "--target",
        dest="targets",
        action="append",
        choices=sorted(inject_mod.TARGETS),
        help="repeatable",
    )
    p.add_argument("--auto", action="store_true", help="detect editors present in this repo")
    p.add_argument("--hours", type=int, default=None)
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--dry-run", action="store_true", help="print the block, write nothing")
    p.add_argument("--remove", action="store_true", help="strip the managed block")
    p.set_defaults(func=cmd_inject)

    p = sub.add_parser("mcp", help="run the stdio MCP server")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("doctor", help="自检：配置 / 网络 / 鉴权 / 数据 / 编辑器 / MCP")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ShadowScribeError as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
