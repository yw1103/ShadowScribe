# 手机端接入协议

> 目标：**让手机端不需要聪明。** 不要求它做 VAD、切片、转码、抽帧或任何智能判断。
> 它只需要"录音 → 上传 → 失败重试"，其余全部在服务端完成。

- **Base URL**：`http://<host>:<port>`（默认端口 `18080`）
- **认证**：除 `/healthz` 外，所有 `/v1/*` 路由都需要 `Authorization: Bearer <SS_TOKEN>`
- **编码**：请求与响应均为 UTF-8；文件上传用 `multipart/form-data` 或裸 body
- **时间**：所有时间戳使用 ISO-8601；建议带时区偏移（`2026-01-08T14:05:00+08:00`）

---

## 1. 端点总览

| 方法 | 路径 | 用途 | 谁用 |
|---|---|---|---|
| `POST` | `/v1/ingest/audio` | 单次上传一段音频（推荐，覆盖 95% 场景） | 手机 |
| `POST` | `/v1/ingest/raw` | 裸 body 上传，省一点 multipart 开销 | 手机（可选） |
| `POST` | `/v1/uploads` | 开一个分片上传会话 | 手机（大文件） |
| `PUT` | `/v1/uploads/{id}/parts/{n}` | 上传第 n 片 | 手机（大文件） |
| `POST` | `/v1/uploads/{id}/complete` | 合并并入库 | 手机（大文件） |
| `GET` | `/v1/jobs/{id}` | 查询处理进度 | 手机（可选） |
| `GET` | `/v1/recordings` | 列出已上传录音与状态 | 手机（可选） |
| `GET` | `/v1/recordings/{id}` | 单条详情（含转写） | 手机（可选） |
| `POST` | `/v1/recordings/{id}/reprocess` | 重新处理 | 调试 |
| `DELETE` | `/v1/recordings/{id}` | 删除录音 | 调试 |
| `GET` | `/v1/context/brief` | 上下文卡片（markdown） | 电脑端 |
| `GET` | `/v1/commitments` | 承诺 / 待办列表 | 电脑端 |
| `PATCH` | `/v1/commitments/{id}` | 修改承诺状态 | 电脑端 |
| `GET` | `/v1/timeline` | 某天的时间轴 | 电脑端 |
| `GET` | `/v1/search` | 检索记忆与转写 | 电脑端 |
| `GET` | `/v1/health` | 服务端状态 | 运维 |
| `POST` | `/v1/speakers/enroll` | 录入主人声纹 | 一次性 |

---

## 2. 推荐接入方式：`POST /v1/ingest/audio`

### 请求

```
POST /v1/ingest/audio
Authorization: Bearer <SS_TOKEN>
Content-Type: multipart/form-data; boundary=...
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|:---:|---|
| `file` | file | ✅ | 音频文件。**任何 ffmpeg 能解的容器都可以**：m4a / aac / opus / ogg / amr / wav / mp3 / webm / 3gp |
| `client_id` | string | 建议 | 稳定的设备标识，如 `pixel-8-a1b2`。用于区分多台设备 |
| `device` | string | 否 | 人类可读描述，如 `Pixel 8 / Android 14` |
| `session_hint` | string | 否 | 自由文本上下文，如 `与老王在会议室`。**会被写进因果记忆的 context，强烈建议填** |
| `recorded_at` | string | 否 | 录音**开始**时间（ISO-8601）。不填则用服务端接收时间。**填了才能正确换算"周四前"这类相对时间** |
| `duration_ms` | int | 否 | 时长。不填服务端会自己探测 |
| `chunk_index` | int | 否 | 若把长录音切成多段，这是第几段（从 0 开始） |
| `chunk_total` | int | 否 | 总段数 |

### 响应 `200`

```json
{
  "recording_id": "9f2c1ab34de54f0d8c7e6a1b2c3d4e5f",
  "job_id": "3b7e8d1f0a2c4e6b8d0f1a3c5e7b9d2f",
  "status": "queued",
  "dedup": false,
  "bytes": 4823194
}
```

`dedup: true` 表示这段音频之前已经传过（服务端按内容 SHA-256 去重），
返回的是**原有的** `recording_id`，此时不会有新的 `job_id`。

### 错误

| 状态码 | 含义 | 手机端应该怎么做 |
|---|---|---|
| `401` | Token 错误 | **不要重试**，提示用户重新配置 |
| `413` | 超过 `SS_MAX_UPLOAD_MB` | 改成分片上传 |
| `422` | 字段缺失或格式错 | **不要重试**，这是客户端 bug |
| `500` | 服务端异常 | 指数退避重试（1s / 4s / 16s，最多 3 次） |
| 连接失败 | 网络不可达 | 存入本地待传队列，等网络恢复 |

### 示例

```bash
curl -X POST http://server:18080/v1/ingest/audio \
  -H "Authorization: Bearer $SS_TOKEN" \
  -F "file=@segment_0014.m4a" \
  -F "client_id=pixel-8-a1b2" \
  -F "device=Pixel 8 / Android 14" \
  -F "session_hint=与老王在会议室" \
  -F "recorded_at=2026-01-08T14:05:00+08:00" \
  -F "chunk_index=14" \
  -F "chunk_total=96"
```

---

## 3. 大文件：分片续传

一整天的录音可能有几百 MB 甚至数 GB，移动网络下极易中断。分片上传让中断只损失一片。

### 3.1 创建会话

```
POST /v1/uploads?filename=day-2026-01-08.m4a&total_parts=20&client_id=pixel-8-a1b2&session_hint=全天
Authorization: Bearer <SS_TOKEN>
```

```json
{ "upload_id": "a1b2c3d4e5f6...", "received_parts": 0, "total_parts": 20 }
```

### 3.2 上传分片

```
PUT /v1/uploads/{upload_id}/parts/{part_number}
Authorization: Bearer <SS_TOKEN>
Content-Type: application/octet-stream

<裸二进制>
```

- `part_number` 从 **0** 开始。
- **重复上传同一片会安全覆盖**——这让"不确定上一片是否成功"的客户端可以无脑重传。
- 响应会返回 `received_parts`，客户端可据此断点续传。

```json
{ "upload_id": "...", "part": 0, "bytes": 5242880, "received_parts": 1, "total_parts": 20 }
```

### 3.3 完成

```
POST /v1/uploads/{upload_id}/complete
Authorization: Bearer <SS_TOKEN>
```

服务端按分片序号数字顺序拼接（`000000`, `000001`, …），然后走与单次上传完全相同的流程，
返回结构也一致。

> 如果 `total_parts` 已声明但分片没传齐，会返回 `400`。

---

## 4. 幂等与重试

- 服务端对**上传内容做 SHA-256 去重**。同一段音频重复上传不会产生重复记忆。
- 所以手机端的重试策略可以非常简单：**不确定就重传**。
- 唯一的例外是用 `SS_LLM_API_KEY` 之外的场景调用 `reprocess?index_memory=true`——
  那会重复写入因果边。普通上传路径永远是安全的。

---

## 5. 推荐的手机端实现策略

### 分段长度

| 场景 | 建议分段 | 理由 |
|---|---|---|
| 会议 / 长时间交谈 | **5–15 分钟** | 单段上传失败代价小；蒸馏窗口默认 12 分钟，配合得好 |
| 全天连续录音 | **10–30 分钟** | 减少请求数；配合分片上传 |
| 碎片化场景 | 按静音自动切 | 最简单：固定 10 分钟强制切 |

分段**不需要**和蒸馏窗口对齐——服务端会自己按时间滑窗合并处理。

> **为什么建议分段而不是传一个大文件**：服务端扛得住，但有代价。实测峰值内存
> ≈ 0.63 GB + 3.3 GB × 音频小时数（2 小时 → 7.2 GB），因为 `faster-whisper`
> 要对整段音频做一次 STFT。超过 `SS_ASR_WINDOW_S`（默认 30 分钟）会自动按窗口
> 解码、内存不再随长度增长，但大文件仍会**串行占住唯一的 worker**（2 小时录音
> 端到端约 19 分钟）。分段上传还让失败重传的代价从"一整天"降到"几分钟"。

### 本地待传队列

```
录音 → 写入本地队列（SQLite/Room/IndexedDB）→ 标记 pending
     → 后台任务：有网时按序上传 → 成功标记 uploaded，失败保留重试
     → 服务端按 sha256 去重，所以重传无副作用
```

**关键点**：上传是**后台任务**，不是录音的一部分。录音绝不因为上传失败而中断。
这样即使一整天没有网络，录音也不会丢。

### 电量与流量

- 音频编码建议 **Opus @ 24–32 kbps 单声道 16 kHz**——语音场景足够，一天约 **250–350 MB**。
  不要用 128 kbps 立体声 AAC，那是 10 倍流量且对 ASR 毫无帮助。
- 只在 **Wi-Fi** 或充电时上传可配置。
- 服务端不解码前不做任何假设，所以你也可以先传低码率版本、之后再补高质量版本（后者会命中不同 sha256，产生新记录——目前不建议这么用）。

### `session_hint` 怎么填

这个字段的价值被低估了。它会被写进记忆的 context，直接影响桌面端检索质量。建议填：

- 会议：`与老王、张总在 3 楼会议室，主题：Q1 排期`
- 电话：`与客户李经理通话，关于合同条款`
- 独处：`独自思考登录页方案`

不要填无信息量的内容（如 `录音`）。

---

## 6. 处理进度

上传成功后音频进入异步队列。手机端如需展示进度：

```
GET /v1/jobs/{job_id}
Authorization: Bearer <SS_TOKEN>
```

```json
{
  "id": "...",
  "recording_id": "...",
  "stage": "pipeline",
  "status": "done",
  "attempts": 1,
  "error": null,
  "result": { "segments": 214, "episodes": 3, "edges": 11, "commitments": 2 }
}
```

`status` 取值：`queued` → `running` → `done` | `failed`。

处理耗时参考（40 核 CPU、`small` 模型）：**约 0.3–0.5 倍音频时长**。
即 10 分钟录音约 3–5 分钟处理完。`large-v3` 约慢 3–4 倍。

---

## 7. 声纹录入（原则二的铺垫）

一次性操作。录制 **15–60 秒**主人单独说话的音频：

```bash
curl -X POST http://server:18080/v1/speakers/enroll \
  -H "Authorization: Bearer $SS_TOKEN" \
  -F "file=@my_voice.m4a" \
  -F "label=主人"
```

成功后，把服务端配置改为 `SS_DIARIZATION=embedding`，之后每段转写都会带上
`speaker: owner | guest`，因果归属（"这是我承诺的" vs "这是对方提的"）会准确得多。

> 需要服务端安装了 `sherpa-onnx` 并放好说话人嵌入模型（`INSTALL_SPEAKERS=true`）。
> 未配置时该接口返回 `503`，其余功能不受影响。

---

## 8. 隐私提醒

手机端与服务端之间是**明文 HTTP**（MVP 阶段）。因此：

- 只在你信任的网络（家庭 Wi-Fi / VPN / 隧道）中上传。
- 不要把服务端端口直接暴露在公网而不设 `SS_TOKEN`。
- 录音内容会被完整上传到服务端。请确认这符合你所在地的法律与你的意愿。
