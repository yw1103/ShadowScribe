# 电脑端接入

状态 C 的完整用法。目标：**打开编辑器新建会话，不写一个字背景，AI 已经知道今天发生了什么。**

---

## 1. 安装与登录

> 客户端**尚未发布到 PyPI**，所以用仓库内的一键脚本（它会依次尝试
> PyPI → 本地仓库 → GitHub）。

**Windows**：双击 `scripts\setup-client.cmd`，按提示填地址和 token。
或者：

```powershell
cd ShadowScribe\scripts
.\setup-client.cmd http://<服务器>:18080 <SS_TOKEN> -Mcp
```

**macOS / Linux**：

```bash
./scripts/setup-client.sh --endpoint http://<服务器>:18080 --token <SS_TOKEN> --mcp
```

手工安装：

```bash
pip install ".\client[mcp]"      # 在仓库目录内
ss login --endpoint http://<服务器>:18080 --token <SS_TOKEN>
ss doctor
```

配置写入 `~/.shadowscribe/config.json`（权限 `600`，因为里面有 token）。

配置解析优先级：**命令行参数 → 环境变量 → 用户配置 → 项目内 `.shadowscribe.json`**。

| 环境变量 | 说明 |
|---|---|
| `SS_ENDPOINT` | 服务端基址 |
| `SS_TOKEN` | Bearer Token |
| `SS_CONFIG` | 覆盖配置文件路径 |

项目内可以放 `.shadowscribe.json`（记得加进 `.gitignore`，里面有 token）：

```json
{ "endpoint": "http://47.102.212.49:18080", "token": "..." }
```

---

## 2. 三种注入方式

影书提供三条路径，按"自动化程度"递增：

### 方式一：手动粘贴（零配置）

```bash
ss brief --copy        # 打印并复制到剪贴板
```

然后粘进任何 AI 对话框。适合偶尔使用、或非编辑器场景（网页版 ChatGPT 等）。

### 方式二：写进编辑器规则文件（推荐）

```bash
ss inject --auto
```

自动探测当前仓库里存在的编辑器标记，把上下文卡片写进对应文件：

| `--target` | 写入文件 | 适用 |
|---|---|---|
| `cursor` | `.cursor/rules/shadowscribe.mdc` | Cursor（带 `alwaysApply: true` 前置元数据） |
| `claude` | `CLAUDE.md` | Claude Code / Claude Desktop |
| `agents` | `AGENTS.md` | Codex、Amp 等遵循 AGENTS.md 约定的工具 |
| `gemini` | `GEMINI.md` | Gemini CLI |
| `copilot` | `.github/copilot-instructions.md` | GitHub Copilot |
| `windsurf` | `.windsurfrules` | Windsurf |
| `cline` | `.clinerules` | Cline / Roo |

**幂等性保证**：内容包裹在显式标记之间，重复执行是**原地替换**，不会追加第二份：

```markdown
<!-- SHADOWSCRIBE:BEGIN -->
...上下文卡片...
<!-- SHADOWSCRIBE:END -->
```

标记之外的内容**永远不会被改动**。移除用：

```bash
ss inject --remove
```

查看会写入什么而不实际写入：

```bash
ss inject --dry-run
```

> **注意**：这是**快照式**注入——文件里是执行 `ss inject` 那一刻的上下文。
> 建议养成习惯：开会回来、或每天开工前跑一次。想做完全自动，用方式三。

### 方式三：MCP（真正的"静默拉取"）

MCP 让编辑器在会话中**主动调用**影书，而不是读一个可能过期的文件。

在 Cursor / Claude Desktop / Claude Code 的 MCP 配置里加：

```json
{
  "mcpServers": {
    "shadowscribe": {
      "command": "ss",
      "args": ["mcp"]
    }
  }
}
```

**Cursor**：`~/.cursor/mcp.json`（全局）或项目内 `.cursor/mcp.json`
**Claude Desktop**：`~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）
或 `%APPDATA%\Claude\claude_desktop_config.json`（Windows）
**Claude Code**：`claude mcp add shadowscribe -- ss mcp`

#### 暴露的工具

| 工具 | 什么时候会被调用 |
|---|---|
| `get_reality_context(hours, max_tokens)` | 用户给出简短、缺背景的指令时（核心） |
| `pending_work_summary()` | 新会话开场，拉 48 小时上下文 |
| `list_open_commitments(status)` | "我还欠什么"、排优先级、写周报 |
| `search_reality(query, limit)` | 提到具体的人/项目/事件，需要确认"当初怎么说的" |
| `get_timeline(day)` | "今天/昨天下午都干了什么" |

另有资源 `shadowscribe://brief` 与 `shadowscribe://commitments`，可被支持 resources
的客户端自动附加。

#### 为什么工具描述写得那么啰嗦

这是刻意的。AI **不会主动调用一个它不理解用途的工具**。
`mcp_server.py` 里每个 docstring 都写明了"在什么情况下调用我"，
server 的 `instructions` 也明确告诉模型：**用户给简短指令时先调
`get_reality_context`，不要反问"你指的是什么"**。

这就是从"有这个工具"到"自动继承上下文"的关键差别。

---

## 3. 全部命令

```bash
ss login [--endpoint URL] [--token T]     # 保存连接信息
ss status                                  # 服务端健康与存量统计
ss brief [--hours N] [--max-tokens N]      # 打印上下文卡片
         [--out FILE] [--copy]
         [--no-quotes] [--no-entities]
ss commitments [--status open|done|cancelled|all] [--limit N]
ss commitments --done <id>                 # 勾掉一项
ss search <query> [--limit N]
ss timeline [--day YYYY-MM-DD]
ss upload <file> [--hint "与老王在会议室"]
ss recordings [--limit N] [--status S]
ss inject [--auto] [--target T]... [--hours N] [--max-tokens N]
          [--dry-run] [--remove]
ss mcp                                     # stdio MCP server
```

全局参数：`--endpoint`、`--token`、`--json`。

---

## 4. 建议的日常习惯

```bash
# 早上开工
ss brief --hours 18 --copy        # 看看昨天下午到今早发生了什么

# 开会回来
ss timeline                       # 确认录音处理完了
ss inject --auto                  # 刷新编辑器里的上下文

# 下班前
ss commitments                    # 今天答应了什么，别忘
```

如果用了 MCP，前两步可以省掉——直接开编辑器问就完了。

---

## 5. 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| `cannot reach ...` | 服务端不可达 | `ss status`；检查隧道/端口是否开着 |
| `401 Unauthorized` | Token 不对 | `ss login --token <新token>` |
| 卡片里没有今天的记录 | 录音还没处理完 | `ss recordings` 看 status；`queued` 说明 worker 在忙 |
| 卡片内容为空但有记录 | 没配 `SS_LLM_API_KEY` | 服务端会降级为"只存转写"。配上 key 后 `reprocess` |
| MCP 工具调用返回"无法读取" | 服务端不可达或 token 失效 | 工具会返回友好提示而不是崩溃，但需要修好连接 |
| `ss inject` 写了两份 | 上一次的结束标记被手动删了 | 手动删掉多余部分后重跑 |
