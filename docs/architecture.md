# 架构

本文解释影书 MVP 的**实现**，以及每个设计决定背后的取舍。概念与愿景见根目录
[README](../README.md)。

---

## 1. 一句话架构

```
现实世界的声音  ──[ 影书：耳朵 + 蒸馏 ]──▶  结构化因果  ──[ causal-memory：记忆 + 推理 ]──▶  可检索的记忆
```

影书**不自建记忆系统**。因果图谱、海马体扩散激活、SWR 巩固、反事实回放这些能力
由 [`causal-memory`](https://github.com/JingxuanC/causal-memory) 提供。
影书解决的是它解决不了的那一半：**从现实世界的原始声音里，把因果抠出来**。

---

## 2. 三态模型 → 代码映射

| 状态 | 代码入口 | 触发方式 |
|---|---|---|
| **A · 静默因果沉淀** | `pipeline/runner.py::Pipeline.process` | 异步作业队列 |
| **B · 声纹授权执行** | *二期*；声纹能力已在 `pipeline/diarize.py` | — |
| **C · 电脑工作贯通** | 服务端的 `/mcp`（`mcp_surface.py`）+ `client/` CLI | 编辑器直连服务器 |

> **MCP 端点跑在服务器上**（`server/shadowscribe/mcp_surface.py`），和它服务的记忆在同一个进程里。
> 电脑端只写一条 URL 配置，不装包、不起进程、不做代理 —— 本机是纯粹的读者。
> 曾有一版把 stdio MCP 放进客户端包、再代理回服务器，那是多余的一跳，而且迫使
> 一台只需要 URL 的机器去 `pip install`。

---

## 3. 数据流

```
手机 ──multipart/chunked──▶ POST /v1/ingest/audio
                                │
                                ├─ SHA-256 去重 ──▶ 命中则直接返回原记录
                                │
                                ├─ 落盘 raw/{id}.{ext}
                                ├─ 写 recordings 表（status=queued）
                                └─ 写 jobs 表（status=queued）
                                            │
        ┌───────────────────────────────────┘
        ▼
  worker 轮询 claim_next_job()
        │
        ├─ 1. audio.normalize()      ffmpeg → 16 kHz 单声道 PCM WAV
        │
        ├─ 2. asr.Transcriber        faster-whisper → 带时间戳的 segments
        │
        ├─ 3. diarize.SpeakerLabeler 声纹余弦相似度 → owner / guest
        │                             （未启用或未录入 → unknown）
        ├─ 4. 写 segments 表
        │
        ├─ 5. distill.Distiller      按时间滑窗切分 → DeepSeek 抽取
        │                            → {title, summary, edges, commitments, decisions}
        │
        ├─ 6. 写 episodes / commitments / entities 表
        │
        └─ 7. memory.write_episode() 因果边 → causal-memory.record_decision()
                                     事实   → causal-memory.record_fact()
```

**读取侧（同一台机器，同一个进程）**：

```
编辑器 ──Streamable HTTP──▶ POST /mcp
                              │
                              ├─ get_reality_context  ──▶ service.build_brief()
                              ├─ list_open_commitments ──▶ service.list_commitments()
                              ├─ search_reality       ──▶ service.semantic_search()
                              ├─ get_timeline         ──▶ service.timeline()
                              │      （以上读影书自己的 SQLite）
                              │
                              └─ search_memory        ──▶ causal-memory 因果图
                                 causal_directory          （同进程的 PyO3 绑定）
```

MCP 端点和它服务的记忆在同一个进程里，所以电脑端**不需要本地代理** ——
这曾经是一层多余的工作量，见 §4.8。

---

## 4. 关键设计决定

### 4.1 为什么用异步，而不是实时

MVP 要验证的唯一命题是：

> **把一天的对话沉淀成记忆之后，电脑前的 AI 是否真的能少问我几句话？**

这个问题只需要"录音结束后若干分钟内出现记忆"就能回答。实时管线带来的是
完全不同的工程量（常驻音频流、端侧 VAD、增量解码、断流重连），却**不改变
这个命题的答案**。所以实时推迟到二期。

副作用是好的：异步让服务端可以用**大模型**换质量。`large-v3` 比实时必须用的
`small` 在中文上准确得多，而异步没有这个约束。

### 4.2 为什么用 SQLite 作业表当队列

不引入 Redis / RabbitMQ / Celery，因为：

- 单机、单用户、低并发——队列深度通常在个位数。
- API 和 worker 已经在共享一个数据卷。
- `jobs` 表天然可查询、可审计、可在崩溃后恢复（`recover_stale_jobs()`）。
- 部署形态简化为"一个卷 + 两个容器"。

代价：不能水平扩展 worker。这在 MVP 阶段是**正确**的取舍——多 worker 需要
分布式锁，而当前瓶颈在 CPU 推理，不在调度。

**并发正确性**：`claim_next_job()` 用 `SELECT ... LIMIT 1` + 立即 `UPDATE status='running'`
在一个事务里完成。SQLite 的写锁保证不会有两个 worker 抢到同一条。
（实际上当前只跑一个 worker 容器。）

### 4.3 为什么记忆层是可插拔的

`memory/base.py` 定义了 `MemoryBackend` 协议，有两个实现：

| 实现 | 何时用 | 检索能力 |
|---|---|---|
| `causal_memory_backend.py` | 生产（默认） | 海马体扩散激活 + BM25 + 反事实 + 因果链回溯 |
| `native.py` | 没有 Rust wheel / 想零依赖跑通 | 中文 bigram 子串匹配 |

工厂函数 `build_backend()` 在 `causal-memory` 导入失败时**自动降级**并打警告。
这意味着一个全新 clone 无论环境多糟糕都能启动——这是"能不能跑起来"和
"跑得好不好"之间的分离。

### 4.4 为什么音频先归一化成 WAV

`audio.normalize()` 把所有输入统一成 **16 kHz 单声道 16-bit PCM**，之后所有模块
都不再需要处理容器格式分支。代价是磁盘上多一份副本，收益是 ASR、声纹、切片三处
代码各自少掉一堆分支和 bug。

### 4.5 声纹为什么先只做"主人 / 对方"二分

完整多方说话人分离（speaker diarization）需要聚类若干个未知说话人。但对**因果
归属**来说，唯一真正致命的错误是主体倒置：

> "**我**承诺周四交付" 被记成 "**老王**承诺周四交付"

这个错误的代价极高（AI 会以为你不用做）。而"老王 vs 张三"的区别在 MVP 阶段
几乎不影响任务执行。所以先做二分：录入主人声纹 → 余弦相似度 → `owner` / `guest`。
多方聚类放二期。

未录入声纹时全部标 `unknown`，蒸馏器会从措辞（"我答应"、"你这边"）里尽力推断，
但会在 `confidence` 上体现不确定性。

### 4.6 为什么蒸馏用严格提示词

一次因果抽取的失败模式是**不对称**的：

- **漏抽**：少一条记忆，用户下次自己补一句，代价低。
- **幻觉**：凭空造出一个用户从没做过的承诺，污染之后**每一次**上下文注入，
  且用户很难发现（因为它看起来很像真的）。代价极高。

所以 `distill.py` 的 system prompt 第一条就是"只记录真实出现过的信息，严禁推测"，
并且明确允许返回空数组。宁可空，不可编。

### 4.7 上下文卡片为什么是 markdown

`build_brief()` 的输出要能直接粘进 Cursor、Claude、终端、或者作为 MCP 工具结果。
markdown 是这四者唯一的公共格式。没有 JSON→渲染的中间层，就没有"渲染出来不对"
这类 bug。

卡片按**价值降序**排列，并且可以按 token 预算**从尾部整段截断**：

1. 进行中的承诺（最重要，且永不截断 —— 只截条目并标注还有几项）
2. 关键决策
3. 因果脉络
4. 话题片段
5. 涉及的人与项目
6. 原话锚点（按录音轮流取，避免一场长会议挤掉短通话）

### 4.8 为什么 MCP 端点跑在服务器上

因为**记忆就在那儿**。端点、因果图和 SQLite 在同一个进程里，把 MCP 放在别处只会
多一跳。

曾经的做法是在客户端包里放一个 stdio MCP 服务，再由它 HTTP 代理回服务器。代价是：
电脑上要 `pip install`、要有 Python 环境、要常驻一个子进程 —— 只为把请求转发给
一台本来就能直连的机器。而且那个常驻进程会锁住自己的可执行文件，让升级失败。

现在编辑器直连 `http://<host>:18080/mcp`，本机零组件。

两个实现上的坑（都在 `mcp_surface.py` 里注释了原因）：

- **不能用 `app.mount("/mcp")`**。Starlette 把它编译成 `^/mcp/(?P<path>.*)$`，
  裸 `/mcp` 永远匹配不上，父路由会回 307；而 MCP 客户端会不会带着 JSON-RPC body
  跟随重定向是未定义的。改成显式 `Route` + 内部路径重写。
- **必须手动组合 lifespan**。Starlette 不会为子应用运行 lifespan，
  不组合的话 streamable-HTTP 的 session manager 根本不会启动。

---

## 5. 数据模型

```
recordings    一段上传的音频 + 处理状态
jobs          作业队列表（stage / status / attempts / error）
upload_sessions  分片上传会话
segments      转写片段（含 speaker 归属）
speakers      录入的声纹向量
episodes      蒸馏出的一个话题片段（含 raw_json 里的 edges / decisions）
commitments   承诺 / 待办
entities      人物、项目、产品等实体索引

--- native 后端专用 ---
mem_edges     因果边（cause → effect, relation）
mem_facts     扁平事实
```

`episodes.raw_json` 保留了完整的抽取结果，这是**有意的冗余**：
它让上下文卡片可以重建而不依赖记忆后端，也让"蒸馏器改进了，但旧记忆不想动"
这种情况有据可查。

完整字段说明见 [`memory-model.md`](memory-model.md)。

---

## 6. 部署拓扑

```
                ┌──────────── 宿主机 ────────────┐
   端口 18080   │  ┌──────────┐   ┌───────────┐  │
  ─────────────▶│  │   api    │   │  worker   │  │
                │  │ uvicorn  │   │  推理循环  │  │
                │  └────┬─────┘   └─────┬─────┘  │
                │       └───────┬───────┘        │
                │           ┌───▼────┐           │
                │           │  卷    │           │
                │           │ /data  │           │
                │           └────────┘           │
                └────────────────────────────────┘
                            │
                    你的隧道 / 反向代理
                            │
                 ┌──────────┴──────────┐
                 │                     │
            /v1/ingest/audio          /mcp
              （手机）            （编辑器，无需 token）
```

`/data` 里有：SQLite 库、原始音频、归一化 WAV、Whisper 缓存、声纹模型、causal-memory 库。

两个容器**共享同一个卷**，通过 SQLite WAL 模式并发读写。API 永远不会被长时间
的推理阻塞。

---

## 7. 长录音：实测容量与窗口化

手机端按 [`ingestion-api.md`](ingestion-api.md) 建议切成 5–15 分钟上传时，这一节
基本与你无关。但**服务端也必须自己扛得住一个 8 小时的大文件**，否则某天手机端
逻辑变了、或者你手动补传了一整天的录音，整台机器会被一起拖下去。

下面每个数字都是在参考服务器（40 vCPU / 62 GB / 无 GPU、`SS_WHISPER_MODEL=small
int8`）上**实测**的，不是估的。

### 7.1 上传：流式落盘，内存与文件大小无关

`api.py::_stream_to` 按 1 MiB 分块读、边写边算 SHA-256，中途超限直接 413 并删掉半截文件。
所以**上传不占内存**，`SS_MAX_UPLOAD_MB`（默认 2048）是磁盘与带宽的闸门，不是内存的。
更大的文件走 `/v1/uploads` 分片续传，分片各自落盘、`complete` 时才拼接。

### 7.2 处理：ASR 是唯一的线性开销

| 阶段 | 2 小时音频实测 | 速度 | 峰值内存 |
|---|---|---|---|
| ffmpeg 归一化（真实 m4a） | 0.2 s（91 s 音频） | **~370x realtime** | 68 MB |
| Whisper ASR | **1036 s** | **6.95x realtime** | **7247 MB** |
| 声纹归属 | 91 s（1169 段） | 12.9 段/秒 | 不增长 |
| 蒸馏 | 3.5 s/窗口 × 10 窗口 | — | 可忽略 |
| **端到端** | **18.9 分钟** | **6.36x realtime** | **7.2 GB** |

纯 ASR 的实时倍率在 0.5 h / 1 h / 2 h 上分别是 7.47x / 7.28x / 6.95x ——
**倍率几乎不随时长衰减**，长录音的代价在内存，不在速度。

CPU 占用约 400%（≈4 核），不是 40 核——瓶颈在模型自身的并行度，不在核数。
所以一次长录音**不会**把整台机器榨干，但会串行占住唯一的 worker。

### 7.3 内存为什么会线性增长

`faster_whisper.transcribe()` 对**整段**音频做一次 STFT：

```python
audio = decode_audio(audio, sampling_rate=16000)   # 整段解码成 float32
features = self.feature_extractor(audio, chunk_length=chunk_length)   # 整段算 mel
```

`chunk_length` 只影响之后的滑窗步进，**不影响这次 STFT 的长度**。于是峰值由
「音频长度」而不是「窗口长度」决定：

| 每小时的中间量 | 大小 |
|---|---|
| 解码后 float32 PCM | ~220 MB |
| 整段 STFT（complex64, 201 bin, 100 fps） | ~580 MB |
| 幅度谱 + 平方的中间量（float32, 200 bin） | ~580–1160 MB |
| mel / log-mel（80 bin） | ~115 MB |
| VAD 拼接 speech chunks 时的 PCM 副本 | ~220 MB |
| **合计（实测拟合）** | **约 3.3 GB / 小时** |

加上常驻的模型与运行时约 630 MB，三个实测点拟合出一条直线
（**峰值 ≈ 0.63 GB + 3.31 GB × 小时数**，三点全部落在 ±1% 内）：

| 音频长度 | 实测峰值 | 拟合值 |
|---|---|---|
| 0.5 h | **2280 MB** | 2280 MB |
| 1 h | **3936 MB** | 3936 MB |
| 2 h | **7247 MB** | 7247 MB |
| 4 h | — | ~14 GB |
| 8 h | — | ~27 GB |
| 24 h（一整天一个文件） | — | ~80 GB ❌ |

### 7.4 所以：超长录音按窗口解码

`asr.py::_transcribe_windows` 在录音长于 `SS_ASR_WINDOW_S`（默认 1800 s）时，
逐窗读取 PCM 并分别喂给模型，时间戳累加偏移后拼回去。

- **内存**从「音频长度」变成「窗口长度」，30 分钟窗口约 2.3 GB，与录音多长无关。
- **切点找静音**：窗口末尾 60 s 内回看，取最长的一段静音（≥300 ms、RMS < -40 dBFS）
  中间下刀，避免把词切成两半；真的没有停顿才退回硬切。
- **语言只测一次**：第一窗检测出的语言固定给后面所有窗口，否则一整天的录音可能
  在某个安静段之后从中文翻成英文。
- **短录音走原路**：不超过窗口长度的文件行为和以前完全一致，不做任何重采样。
- 窗口化只在**归一化后的 16 kHz 单声道 PCM WAV** 上生效——这正是管线第一步的产物。

窗口大小是内存与调用次数的取舍：调小省内存但窗口变多，调大反之。`0` 关闭窗口化
（回到整段解码，只有在内存充裕时才该这么做）。

### 7.5 磁盘

| 内容 | 每音频小时 |
|---|---|
| 归一化 WAV（16 kHz 单声道 s16） | ~115 MB（1 小时实测 115,202,014 字节） |
| 原始上传件（Opus 24–32 kbps） | ~15 MB |
| 模型（Whisper small + CAM++） | 一次性 ~490 MB |

一整天（16 h）约 2 GB。`SS_KEEP_AUDIO=false` 可在蒸馏后立刻删音频；
`shadowscribe prune` 按 `SS_AUDIO_RETENTION_DAYS`（默认 30 天）清理，**只删音频，
不删转写与记忆**。

---

## 8. 已知限制

| 限制 | 影响 | 计划 |
|---|---|---|
| `/mcp` 不校验 token | 知道地址就能读全部记忆 | 单人自用是有意的；`SS_MCP_REQUIRE_TOKEN=true` 可打开 |
| 无端侧加密 | 音频明文上传 | MVP 有意为之，见 README「隐私与边界」 |
| 单 worker | 一个 8 小时文件会占住队列 70 分钟 | 二期加分布式锁 |
| 长录音没有中间进度 | `GET /v1/jobs/{id}` 在整段处理期间只显示 running | 每次窗口落一次进度 |
| 失败要重跑整段 ASR | 蒸馏阶段崩了，转写会重来（归一化会跳过） | 窗口级检查点 |
| 声纹每段打开一次 WAV | 1169 段 = 1169 次 `wave.open`，2 小时约 91 s | 一次读入或持有句柄 |
| 无多方说话人聚类 | 只能区分"主人/对方" | v0.2 |
| 承诺不会自动闭环 | 完成了要手动 `ss commitments --done` | v0.2（从后续对话识别"已完成"） |
| `reprocess --index-memory` 会重复写因果边 | 重复记忆 | 需要幂等边写入 |
| 中文以外的语言未经充分验证 | 日/英可用但未调优 | 欢迎反馈 |
