# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

尚未发布。以下都是 v0.1.0 之后、在真实部署上跑出来并修掉的问题。

### Changed

- **MCP 端点搬到服务器上**（`server/shadowscribe/mcp_surface.py`），暴露在 `/mcp`。
  电脑端只需一条 URL：`{"url": "http://<host>:18080/mcp"}` —— 不装包、不起进程、不做代理。
  `/mcp` 目前**不校验 token**（单人自用），`SS_MCP_REQUIRE_TOKEN=true` 可打开。
- **删除客户端的 stdio MCP 服务**（`ss mcp` 与 `client/.../mcp_server.py`）。
  它把请求 HTTP 代理回同一台服务器，是多余的一跳，还迫使一台只需要 URL 的机器去
  `pip install`。客户端现在只剩 `httpx`，且**变成可选**。
- 声纹默认启用（`SS_DIARIZATION=embedding`），中文 CAM++ 模型放在数据卷里。

### Added

- `ss setup`：写 MCP URL + 一段永不变化的静态指令，合并式写入不覆盖已有 MCP server
- `ss doctor`：自检配置 / 网络 / 鉴权 / 数据新鲜度 / 编辑器 / **MCP 端点**
- 简繁归一化（`pipeline/text.py`），避免 `登录页` 与 `登錄頁` 裂成两条记忆
- `INSTALL_MCP` 构建开关；`test_config_defaults.py` 守住「默认值必须是合法默认值」
- **超长录音分窗解码**（`SS_ASR_WINDOW_S`，默认 1800 s）。`faster-whisper` 对整段
  音频做一次 STFT，峰值内存实测 ≈ 0.63 GB + 3.3 GB × 音频小时数（0.5/1/2 小时
  三个点精确共线），一整天一个文件要约 80 GB。分窗后峰值只取决于窗口大小，
  切点在静音处、语言只检测一次。同一个 2 小时文件实测
  **7247 MB → 2577 MB，且略快（1036 s → 966 s）**。数据见 `docs/architecture.md` §7

### Fixed

- **`actor` 主体倒置**：没有声纹时提示词会把「我承诺的」记成「对方承诺的」。
  规则重写为围绕「谁欠谁一个交付」判断，并加了 schema 字面词与自相矛盾的兜底
- **`hf-mirror` 下载模型 401**：新版 `huggingface_hub` 走 Xet，镜像不代理，必须
  `HF_HUB_DISABLE_XET=1`
- **`sherpa-onnx` 1.13 把 `compute_embedding()` 改成 `compute()`**：旧调用被 except
  吞掉，表现为"声纹提取失败（音频太短）"，其实是 API 改名
- **worker 缓存声纹不刷新**：重新录入后重跑，相似度数值一字不差 —— 用户按文档操作
  却完全没效果
- **Windows 安装脚本无法运行**：`.ps1` 缺 UTF-8 BOM，PS 5.1 按 GBK 读导致语法错误；
  且双击 `.ps1` 本就不会执行
- **apt 源拖慢每次重建**：apt 层在所有 Python 层之上，`APT_MIRROR` 可配
- **短对话在卡片里消失**：原话锚点取全局 top-N，一场长会议挤掉了短通话
- **`ss brief` 在 Windows 控制台崩溃**：GBK 编不了卡片里的 emoji
- **`/mcp` 裸路径 307**：Starlette 的 `Mount` 正则要求尾斜杠，改为显式 Route +
  内部路径重写
- **长录音会把宿主机内存吃光**：`faster_whisper.transcribe(chunk_length=…)` 的
  `chunk_length` 只影响滑窗步进，整段 STFT 照旧 —— 一个 8 小时文件要约 27 GB，
  而 worker 没有内存上限。改为分窗解码

### Removed

- `ss mcp`、客户端 `mcp` 额外依赖、`pending_work_summary` 工具、MCP resources

## [0.1.0] — 2026-01-08

首个 MVP。回答一个问题：**把一天的对话沉淀成记忆之后，电脑前的 AI 是否真的能少问我几句话？**

### Added

**服务端 — 状态 A：静默因果沉淀**

- 接入 API：`POST /v1/ingest/audio`（multipart）、`POST /v1/ingest/raw`（裸 body）、
  分片续传（`/v1/uploads` 三段式），全部按内容 SHA-256 幂等去重
- 异步作业管线：ffmpeg 归一化 16 kHz 单声道 → faster-whisper 转写 →
  声纹说话人归属 → LLM 因果蒸馏 → 记忆入库
- 基于 SQLite 的作业队列（`jobs` 表），带崩溃恢复（`recover_stale_jobs`）
- 严格提示词的因果蒸馏器：输出 `cause → effect` 边（`caused`/`enabled`/`prevented`/`no_effect`）、
  决策、承诺（含 `due_text` 与换算后的 `due_date`）、扁平事实、实体
- 无 LLM key 时降级为"只存转写"而不失败（`raw_json.degraded = true`）
- 声纹录入 `POST /v1/speakers/enroll` 与余弦相似度主人/对方二分归属
- 可插拔记忆层：`causal-memory` PyO3 绑定（默认）与 `native` SQLite 兜底，自动降级
- 上下文卡片 `GET /v1/context/brief`，按 token 预算整段截断
- `GET /v1/commitments` / `PATCH /v1/commitments/{id}` / `GET /v1/timeline` / `GET /v1/search`
- 运维 CLI `shadowscribe`：`serve` / `worker` / `ingest` / `brief` / `stats` / `prune` / `doctor`

**电脑端 — 状态 C：零赘述上下文贯通**

- `ss` CLI：`login` / `status` / `brief` / `commitments` / `search` / `timeline` /
  `upload` / `recordings` / `inject` / `mcp`（`mcp` 已在 Unreleased 中删除）
- `ss inject`：把卡片幂等写入 Cursor / CLAUDE.md / AGENTS.md / GEMINI.md /
  Copilot / Windsurf / Cline 的规则文件，带显式 managed markers
- MCP 工具：`get_reality_context`、`list_open_commitments`、`search_reality`、
  `get_timeline`（当时以 stdio 形式运行在客户端，已在 Unreleased 中改为服务端 HTTP）

**部署与文档**

- Docker Compose 单卷双容器部署（api + worker 共享 `/data`）
- `shadowscribe doctor` 自检：ffmpeg / whisper / 记忆后端 / LLM / 声纹 / 数据目录
- 中文优先文档：README、架构、手机端接入协议、记忆模型、电脑端接入、部署、路线图
- MIT License

### Known limitations

- 实时语音与声纹授权执行（状态 B）未实现，见 [roadmap](docs/roadmap.md)
- 仅区分"主人/对方"，未做多方说话人聚类
- 承诺不会自动闭环，需手动 `ss commitments --done <id>`
- `reprocess --index-memory` 会重复写入因果边
- 音频明文上传，无端侧加密（MVP 阶段有意为之）

[Unreleased]: https://github.com/yw1103/ShadowScribe/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/yw1103/ShadowScribe/releases/tag/v0.1.0
