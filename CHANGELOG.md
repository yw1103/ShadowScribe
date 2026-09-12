# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
- 上下文卡片 `GET /v1/context/brief`，按 token 预算从尾部整段截断
- `GET /v1/commitments` / `PATCH /v1/commitments/{id}` / `GET /v1/timeline` / `GET /v1/search`
- 运维 CLI `shadowscribe`：`serve` / `worker` / `ingest` / `brief` / `stats` / `prune` / `doctor`

**电脑端 — 状态 C：零赘述上下文贯通**

- `ss` CLI：`login` / `status` / `brief` / `commitments` / `search` / `timeline` /
  `upload` / `recordings` / `inject` / `mcp`
- `ss inject`：把卡片幂等写入 Cursor / CLAUDE.md / AGENTS.md / GEMINI.md /
  Copilot / Windsurf / Cline 的规则文件，带显式 managed markers
- MCP server（stdio）：`get_reality_context`、`pending_work_summary`、
  `list_open_commitments`、`search_reality`、`get_timeline`，以及
  `shadowscribe://brief`、`shadowscribe://commitments` 资源

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
