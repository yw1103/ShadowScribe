# AGENTS.md — 在 ShadowScribe 仓库里工作

给 AI 编码助手（Cursor / Claude Code / Codex / DSH 等）的项目说明。

---

## 这是什么

影书 ShadowScribe：**无感因果外脑**。手机静默录音 → 服务器异步转写并提炼因果 →
电脑前的 AI 零成本继承现实上下文。核心设计见 [`docs/architecture.md`](docs/architecture.md)。

**改任何代码之前先读这三条不可妥协的原则** —— 很多"合理"的改动其实违反它们：

1. **绝对静默旁听** —— 系统不发声、不弹窗、不主动建议。
   任何引入主动行为的功能都要先问清楚为什么必要。
2. **声纹绝对主权** —— 不执行非主人声纹授权的指令。
3. **零赘述上下文贯通** —— 用户不应该在"现实"和"AI"之间当搬运工。
   **任何要求用户定期手动运行的命令都是设计缺陷**，包括看起来很方便的那种。

### 架构上的硬约束

**服务器拥有全部状态，电脑端是纯读者。** MCP 端点和记忆在服务器同一个进程里
（`server/shadowscribe/mcp_surface.py`）。不要往客户端加需要常驻的东西 ——
曾经有一个 stdio MCP 代理，代价是电脑上要 `pip install`、要有 Python 环境、
要跑一个子进程，只为把请求转发给一台本来就能直连的机器。

**不要在客户端重复实现服务端已有的能力。**

---

## 常用命令

```bash
# 服务端
cd server && pip install -e '.[memory,speakers,mcp,dev]'
pytest -q                       # 155 个测试，不需要 ffmpeg / 模型 / API key
ruff check . && ruff format --check .

# 电脑端
cd client && pip install -e '.[dev]'
pytest -q                       # 76 个测试
ruff check . && ruff format --check .
```

CI 跑 Python 3.10 / 3.11 / 3.12，外加一次 Docker 构建 + 容器冒烟测试。

---

## 改代码前必须知道的事

### 依赖是可选的，不是装饰

`numpy`、`sherpa-onnx`、`opencc`、`causal-memory`、`mcp` 全是**可选依赖**，
代码里都有优雅降级。原因不是洁癖：CI 只用精简依赖跑测试，
这样才保证"全新 clone 一定能跑起来"。

新增可选依赖时，必须同时提供降级路径**和**一个不依赖它的测试。

### 快速失败的代价是不对称的

蒸馏器（`pipeline/distill.py`）宁可返回空，也不许编造。
一条幻觉出来的承诺会污染**之后每一次**上下文注入，而且用户很难发现。
同理，声纹归属宁可 `unknown`，不要猜。

写这类代码时，默认选择"漏掉"而不是"猜一个"。

### 上游库会悄悄把整个文件读进内存

`faster_whisper` 的 `transcribe()` 对**整段**输入做一次 STFT，`chunk_length`
只影响之后的滑窗步进、**不缩短这次 STFT**。实测峰值内存 ≈ **0.63 GB +
3.3 GB × 音频小时数**（0.5 h→2.3 GB，1 h→3.9 GB，2 h→7.2 GB，三点精确共线）。
所以超过 `SS_ASR_WINDOW_S` 的录音必须走 `asr.py::_transcribe_windows` 分窗解码。

教训是通用的：**"流式 API" 的语义要读源码确认，不能从参数名推断**。
`docs/architecture.md` §7 有完整的实测表和公式。

### 注释解释"为什么"

本项目注释密度高于典型 Python 项目，这是有意的。
每个模块顶部说明**它为什么存在**；在"本来可以更简单但故意没这么做"的地方写清取舍。

反例：`# 遍历 segments`
正例：`# 传 labels=None 而不是空列表：蒸馏器会把 None 理解成"没有声纹信息，
      请从措辞推断"，而空列表会让它以为每句话都属于未知说话人。`

### Windows 上有几个坑，都踩过了

- **`.ps1` 必须带 UTF-8 BOM**。PowerShell 5.1 没有 BOM 时按 ANSI 代码页读，
  中文会变乱码并**导致语法错误**。`scripts/setup-client.ps1` 有测试守着这点。
- **`.cmd` 必须无 BOM、必须 CRLF、必须纯 ASCII**（cmd.exe 按 OEM 代码页读批处理）。
- **双击 `.ps1` 不会运行**（Windows 不关联），所以提供了 `.cmd` 启动器。
- **Python 里 spawn 子进程要显式 `encoding="utf-8"`**，否则中文 Windows 用 GBK 解码。
- **`shutil.which("bash")` 在 Windows 上找到的是 WSL 的**，读不了 `D:\` 路径。
- **重装本地包必须 `--force-reinstall --no-cache-dir`**。版本号在提交之间不变，
  而 pip 的 wheel 缓存和 setuptools 的 `build/` 都按版本号索引 —— 不加这两个参数，
  pip 会"成功"但装的是旧代码。
- **`ss.exe` 会被占用**。如果 Cursor 还跑着上一次注册的 MCP 进程，pip 卸载会
  `WinError 32` 并**整体中止，却照样打印 "Successfully built"**。
  **判断安装成功要看退出码，不是输出文本。**

### 测试要能看见默认值和环境

两个真实盲区，都因为测试环境"太干净"而漏掉了缺陷：

- `conftest.py` 给全套测试设了 `SS_MEMORY_BACKEND`，于是**默认值从没被验证过** ——
  一个被写坏的默认值一直藏到 Docker 构建才发现。`test_config_defaults.py` 用
  清空环境的方式构造 `Settings` 来堵这个洞。
- 客户端测试没清 `SS_ENDPOINT` / `SS_TOKEN`，开发者一旦导出这些变量就会假失败。
  用 `no_config_env` fixture 统一清理。

加测试时问一句：**这个断言在什么样的环境下会假通过？**

### 上游 API 会改名，别硬编码

已经踩过三次，全部用"调用时解析"而不是"导入符号"解决：

| 包 | 变化 |
|---|---|
| `mcp` | 2.x 把 `FastMCP` 改成 `MCPServer` |
| `sherpa-onnx` | 1.13 把 `compute_embedding()` 改成 `compute()` |
| `huggingface_hub` | 新版默认走 Xet，国内镜像不代理，必须 `HF_HUB_DISABLE_XET=1` |

**更糟的是"优雅降级"会把这类错误藏起来**：`sherpa-onnx` 那次，`AttributeError`
被宽 `except` 吞掉，对外表现成"音频太短"，排查了很久。降级路径一定要在日志里
留下**可区分**的原因。

### 改动会影响别人的机器

- 新增 `SS_*` 配置 → 同步 `.env.example` 和 README 配置表
- 改接口 → 同步 `docs/ingestion-api.md`
- 改数据结构 → 同步 `docs/memory-model.md`
- 完成路线图条目 → 同步 `docs/roadmap.md`
- 改 `ss` 子命令 → `client/tests/test_cli.py` 里有命令清单，会挡住漂移

---

## 绝对不要提交

- **真实 token / API key**。`docs/operations.md` 里的地址可以是真实的，token 必须是占位符。
- **真实对话内容**（涉及他人隐私）。issue 和测试用合成样例。
- **虚拟环境**。`.gitignore` 里有 `.venv*/`，用 `.venv-ci` 这类名字也不会漏。

---

## 提交前

```bash
ruff check . && ruff format --check . && pytest -q
```

提交信息用 [Conventional Commits](https://www.conventionalcommits.org/)。
正文请写**为什么**，尤其是修 bug 时：`docs/` 里没有的现场信息，只有提交信息能留下。

<!-- SHADOWSCRIBE:BEGIN -->
# 影书 ShadowScribe · 现实上下文

这台机器上运行着 `shadowscribe` MCP 服务，它保存着主人**现实世界**（会议、电话、
线下对话）里达成的决策、做出的承诺和因果关系。这些内容用户不会、也不该再复述。

**开始处理任务前，先调用 `get_reality_context`。**
- 问"我还欠谁什么"、"待办" → `list_open_commitments`
- 提到具体的人/项目/事件 → `search_reality`
- 问"今天/昨天干了什么" → `get_timeline`

用户的指令通常很短、缺背景 —— 背景不在他脑子里等你追问，而在影书里。
**不要反问"你指的是什么"，先拉上下文。**

<!-- SHADOWSCRIBE:END -->
