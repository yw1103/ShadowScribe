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
.\setup-client.cmd http://<服务器>:18080 <SS_TOKEN>
```

**macOS / Linux**：

```bash
./scripts/setup-client.sh --endpoint http://<服务器>:18080 --token <SS_TOKEN>
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

## 2. 两条路径，先选对

影书有**两种完全不同的注入方式**，混淆它们会得到一个每天都要手动维护的系统 —— 那正是本项目要消灭的东西。

| | **① 静态指令 + MCP**（无感） | **② 快照**（应急） |
|---|---|---|
| 命令 | `ss setup` **跑一次** | `ss inject` 每次都要重跑 |
| 写什么 | 一段**永不变化**的指令："去调 MCP 拿上下文" | 当时那一刻的上下文卡片 |
| 数据新鲜度 | **实时**（每次对话现拉） | 停在执行命令的那一刻 |
| 你需要做什么 | **什么都不用做** | 记得手动刷新 |

**默认走 ①。** ② 只用于不支持 MCP 的客户端，或者你想手动贴进网页版 ChatGPT。

---

### 路径 ①：`ss setup`（跑一次，永久无感）

```bash
cd 你的项目
ss setup
```

它做三件事，都不需要重复执行：

1. **注册 MCP** —— 写进 `~/.cursor/mcp.json`（Cursor 全局）和 Claude Desktop 配置。
   合并式写入，**不会动你已有的其他 MCP server**，写前自动备份。
2. **写静态指令** —— 项目级 `.cursor/rules/shadowscribe.mdc`（`alwaysApply: true`）
   和 `AGENTS.md`；全局的 `~/.claude/CLAUDE.md`。
3. **打印一段文字**，让你粘贴到 Cursor → Customize → Rules → **User Rules**。

> **为什么最后一步要手动？** Cursor 唯一有文档保证的*全局*机制是 User Rules，
> 它存在 Cursor 自己的数据库里，没有公开的文件接口。项目级规则（`.cursor/rules`）
> 是自动的，但只覆盖那一个项目。粘贴一次 User Rules，之后所有项目都自动生效。

静态指令的内容长这样 —— 它**只描述"去哪里拿"，不含任何记忆**，所以永远不会过期：

```markdown
**开始处理任务前，先调用 `get_reality_context`。**
- 问"我还欠谁什么"、"待办" → `list_open_commitments`
- 提到具体的人/项目/事件 → `search_reality`

用户的指令通常很短、缺背景 —— 背景不在他脑子里等你追问，而在影书里。
**不要反问"你指的是什么"，先拉上下文。**
```

配置完成后，在 Cursor 里**直接说事**即可，它会自己去拉实时上下文。

---

### 路径 ②：`ss inject`（快照，给没有 MCP 的客户端）

```bash
ss brief --copy              # 只打印 + 复制到剪贴板，不动任何文件
ss inject --auto             # 写进编辑器规则文件
```

| `--target` | 写入文件 | 适用 |
|---|---|---|
| `cursor` | `.cursor/rules/shadowscribe.mdc` | Cursor |
| `claude` | `CLAUDE.md` | Claude Code / Desktop |
| `agents` | `AGENTS.md` | Codex、Amp 等 |
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

标记之外的内容**永远不会被改动**。移除用 `ss inject --remove`，
预览用 `ss inject --dry-run`。

> ⚠️ **这是快照，会过期。** 文件里是执行命令那一刻的上下文，之后现实世界继续在走。
> 快照块里也写了这句提醒，避免读到的人把一周前的卡片当成当前状态。
> 能用 MCP 就别用这条。

---

## 3. MCP 工具清单

`ss setup` 已经帮你注册好了。手工注册的话，配置长这样：

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

写入位置：

- **Cursor**：`~/.cursor/mcp.json`（全局）或项目内 `.cursor/mcp.json`
- **Claude Desktop**：macOS `~/Library/Application Support/Claude/claude_desktop_config.json`
  · Windows `%APPDATA%\Claude\claude_desktop_config.json`
- **Claude Code**：`claude mcp add shadowscribe -- ss mcp`

> `ss setup` 写入的是 `ss` 的**绝对路径**而不是裸 `ss`：从开始菜单启动的编辑器
> 继承的环境和你装客户端的那个终端不一样，裸命令经常找不到。

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
