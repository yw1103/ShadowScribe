#!/usr/bin/env bash
#
# 影书 ShadowScribe —— 电脑端一键安装与配置（macOS / Linux）。
#
#   ./setup-client.sh --endpoint http://47.102.212.49:18080 --token <SS_TOKEN> [--mcp] [--inject]
#
# 把「本地通过内网穿透地址跟服务器沟通」压成一条命令：
# 装客户端 → 写配置 → 连通性自检。
#
set -uo pipefail

ENDPOINT=""
TOKEN=""
DO_MCP=0
DO_INJECT=0
NO_INSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --endpoint) ENDPOINT="${2:-}"; shift 2 ;;
    --token)    TOKEN="${2:-}"; shift 2 ;;
    --mcp)      DO_MCP=1; shift ;;
    --inject)   DO_INJECT=1; shift ;;
    --no-install) NO_INSTALL=1; shift ;;
    -h|--help)  sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

if [ -z "$ENDPOINT" ] || [ -z "$TOKEN" ]; then
  echo "用法：$0 --endpoint http://host:18080 --token <SS_TOKEN> [--mcp] [--inject]" >&2
  exit 2
fi
ENDPOINT="${ENDPOINT%/}"

step() { printf '\n==> %s\n' "$1"; }
good() { printf '    OK   %s\n' "$1"; }
warn() { printf '    !!   %s\n' "$1"; }
die()  { printf '    FAIL %s\n' "$1" >&2; [ -n "${2:-}" ] && printf '\n    试试这个：\n      %s\n' "$2" >&2; exit 1; }

printf '\n  影书 ShadowScribe · 电脑端安装\n  服务端：%s\n' "$ENDPOINT"

# ---------------------------------------------------------------- 1. Python --
step "1/5 检查 Python"
PY=""
for cand in python3 python; do
  command -v "$cand" >/dev/null 2>&1 || continue
  if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
    PY="$(command -v "$cand")"
    good "找到 $PY  ($("$cand" -c 'import sys;print("%d.%d"%sys.version_info[:2])'))"
    break
  fi
done
[ -n "$PY" ] || die "没找到 Python 3.10+" "macOS: brew install python@3.12   ·   Ubuntu: sudo apt install python3 python3-venv python3-pip"

# -------------------------------------------------------------- 2. install --
if [ "$NO_INSTALL" = "1" ]; then
  step "2/5 跳过安装（--no-install）"
else
  step "2/5 安装客户端（shadowscribe-client[mcp]）"
  if "$PY" -m pip install --user --upgrade "shadowscribe-client[mcp]" >/tmp/ss-pip.log 2>&1; then
    good "安装完成"
  elif "$PY" -m pip install --user --upgrade --break-system-packages "shadowscribe-client[mcp]" >>/tmp/ss-pip.log 2>&1; then
    good "安装完成（用了 --break-system-packages）"
  else
    tail -15 /tmp/ss-pip.log >&2
    die "pip 安装失败" "$PY -m pip install --user --upgrade \"shadowscribe-client[mcp]\" -i https://pypi.tuna.tsinghua.edu.cn/simple"
  fi
fi

# ---------------------------------------------------------------- 3. find ss --
step "3/5 定位 ss 命令"
SS="$(command -v ss 2>/dev/null || true)"
# `ss` is also a common alias for `iproute2`'s socket tool — make sure it is ours.
if [ -n "$SS" ] && ! "$SS" --help 2>&1 | grep -qi "shadowscribe\|ShadowScribe"; then
  warn "$SS 不是影书的 ss（可能是 iproute2 的），另找一个"
  SS=""
fi
if [ -z "$SS" ]; then
  SS="$("$PY" -m site --user-base 2>/dev/null)/bin/ss"
  [ -x "$SS" ] || SS=""
fi
[ -n "$SS" ] || die "装完了但找不到 ss 命令" "手动找：$PY -c \"import shadowscribe_client,os;print(os.path.dirname(shadowscribe_client.__file__))\""
good "ss = $SS"

case ":$PATH:" in
  *":$(dirname "$SS"):"*) ;;
  *) warn "$(dirname "$SS") 不在 PATH；下面配置时会直接用完整路径"
     warn "永久生效：把 export PATH=\"$(dirname "$SS"):\$PATH\" 加进 ~/.zshrc 或 ~/.bashrc" ;;
esac

SS_RUN=("$SS")

# ------------------------------------------------------------- 4. configure --
step "4/5 写入连接配置"
"${SS_RUN[@]}" login --endpoint "$ENDPOINT" --token "$TOKEN" \
  || die "配置写入或连通性检查失败" "确认服务端在跑、端口已开：curl -s $ENDPOINT/healthz"

# ----------------------------------------------------------------- 5. doctor --
step "5/5 自检"
"${SS_RUN[@]}" doctor
DOCTOR=$?

# ----------------------------------------------------------------- optional --
if [ "$DO_MCP" = "1" ]; then
  step "附加：注册 MCP 到 Cursor"
  MCP_DIR="$HOME/.cursor"
  MCP_FILE="$MCP_DIR/mcp.json"
  mkdir -p "$MCP_DIR"
  [ -f "$MCP_FILE" ] && cp "$MCP_FILE" "$MCP_FILE.bak" && warn "原文件已备份为 mcp.json.bak"
  "$PY" - "$MCP_FILE" "$SS" <<'PY'
import json, pathlib, sys
path, ss = pathlib.Path(sys.argv[1]), sys.argv[2]
try:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except Exception:
    data = {}
data.setdefault("mcpServers", {})["shadowscribe"] = {"command": ss, "args": ["mcp"]}
path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"    写入 {path}")
PY
fi

if [ "$DO_INJECT" = "1" ]; then
  step "附加：往当前目录注入上下文"
  "${SS_RUN[@]}" inject --auto
fi

# ------------------------------------------------------------------- 收尾 --
printf '\n'
if [ "$DOCTOR" = "0" ]; then
  printf '  装好了。\n'
else
  printf '  装好了，但自检有项目没通过 —— 看上面的 [MISS] 和建议。\n'
fi
cat <<'EOF'

  日常两条命令：
    ss brief --copy     看最近发生了什么，并复制到剪贴板
    ss inject --auto    把上下文写进 Cursor / CLAUDE.md，之后新会话自动带上

  更多：ss --help    ·    文档：https://github.com/yw1103/ShadowScribe

EOF
