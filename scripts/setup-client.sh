#!/usr/bin/env bash
#
# 影书 ShadowScribe —— 电脑端一键安装与配置（macOS / Linux）。
#
#   ./setup-client.sh --endpoint http://host:18080 --token <SS_TOKEN>
#
# 装客户端 → 写配置 → 自检 → 接线（注册 MCP + 写静态指令）。
#
# 最后一步才是「无感」的关键：它写的是**永不变化的静态指令**，让编辑器自己按需
# 调 MCP 拿实时上下文。之后你不需要再运行任何影书命令。
# 手动跑 `ss inject` 那种「快照」方式只给不支持 MCP 的客户端用。
#
set -uo pipefail

ENDPOINT=""
TOKEN=""
NO_INSTALL=0
NO_SETUP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --endpoint) ENDPOINT="${2:-}"; shift 2 ;;
    --token)    TOKEN="${2:-}"; shift 2 ;;
    --no-setup) NO_SETUP=1; shift ;;
    --no-install) NO_INSTALL=1; shift ;;
    -h|--help)  sed -n '2,12p' "$0"; exit 0 ;;
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

  # 依次尝试三个来源。兜底不是可有可无的：这个包还没发布到 PyPI，只写
  # `pip install shadowscribe-client` 的话用户拿到的是 "No matching
  # distribution found" —— 而他往往就站在仓库目录旁边，本可以一行装好。
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  LOCAL_CLIENT="$(dirname "$SCRIPT_DIR")/client"

  SPECS=("shadowscribe-client[mcp]")
  LABELS=("PyPI")
  if [ -f "$LOCAL_CLIENT/pyproject.toml" ]; then
    SPECS+=("$LOCAL_CLIENT[mcp]")
    LABELS+=("本地仓库 $LOCAL_CLIENT")
  fi
  SPECS+=("shadowscribe-client[mcp] @ git+https://github.com/yw1103/ShadowScribe.git#subdirectory=client")
  LABELS+=("GitHub")

  INSTALLED=0
  for i in "${!SPECS[@]}"; do
    printf '    尝试：%s\n' "${LABELS[$i]}"
    if "$PY" -m pip install --user --upgrade "${SPECS[$i]}" >/tmp/ss-pip.log 2>&1; then
      good "从 ${LABELS[$i]} 安装成功"
      INSTALLED=1
      break
    fi
    # Debian/Ubuntu 的 externally-managed 环境需要显式放行
    if "$PY" -m pip install --user --upgrade --break-system-packages "${SPECS[$i]}" >>/tmp/ss-pip.log 2>&1; then
      good "从 ${LABELS[$i]} 安装成功（用了 --break-system-packages）"
      INSTALLED=1
      break
    fi
    warn "${LABELS[$i]} 不可用，试下一个"
  done

  if [ "$INSTALLED" = "0" ]; then
    tail -15 /tmp/ss-pip.log >&2
    die "三个来源都装不上" "$PY -m pip install --user --upgrade \"\$PWD/client[mcp]\" -i https://pypi.tuna.tsinghua.edu.cn/simple"
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

# ------------------------------------------------------------------ 6. setup --
# 这一步才是「无感」的关键，而且只需要跑这一次。
#
# 之前我把 `ss inject`（写快照）当成日常命令，那是错的：快照一过期就要重跑，
# 等于让用户每天手动搬运自己的上下文。`ss setup` 写的是**永不变化的静态指令**，
# 它让编辑器自己去调 MCP 拿实时数据，所以之后什么都不用做。
if [ "$NO_SETUP" = "1" ]; then
  step "6/6 跳过接线（--no-setup）"
else
  step "6/6 接线：注册 MCP + 写静态指令"
  "${SS_RUN[@]}" setup || warn "接线有项目没成功，看上面的输出"
fi

# ------------------------------------------------------------------- 收尾 --
printf '\n'
if [ "$DOCTOR" = "0" ]; then
  printf '  装好了。\n'
else
  printf '  装好了，但自检有项目没通过 —— 看上面的 [MISS] 和建议。\n'
fi
cat <<'EOF'

  之后你不需要再运行任何影书命令。
  在 Cursor 里直接说事，它会自己调 get_reality_context 拿现实上下文。

  可选：想手动看一眼现实里发生了什么 ——
    ss brief --copy     打印最近上下文并复制到剪贴板

  更多：ss --help    ·    文档：https://github.com/yw1103/ShadowScribe

EOF
