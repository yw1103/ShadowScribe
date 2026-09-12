<div align="center">

# 影书 ShadowScribe

**无感因果外脑 · 让 AI 记住因果，而不是让你重复背景**

[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)
[![Status: v0.1.0 MVP](https://img.shields.io/badge/status-v0.1.0%20MVP-orange.svg)](../../releases)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](server/pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-ready-6E56CF.svg)](docs/desktop-integration.md)
[![Memory: causal-memory](https://img.shields.io/badge/memory-causal--memory-2ea44f.svg)](https://github.com/JingxuanC/causal-memory)

[核心概念](#-它到底是什么) · [三态模型](#-三态模型) · [快速开始](#-快速开始) · [接入协议](docs/ingestion-api.md) · [路线图](docs/roadmap.md)

</div>

---

## 🎯 它到底是什么

影书不是聊天机器人，也不是靠唤醒词触发的智能音箱。

它是常驻在物理世界底层的**隐形数字书记官**：平时绝对静默，像影子一样记录现实中发生的多方对话，持续沉淀成**因果记忆图谱**；当你坐回电脑前准备干活时，AI 已经默认掌握了线下发生的一切——**你不需要再写一个字去交代背景**。

> 消灭人机协作中最反人性的环节：**人类给机器写汇报说明**。

### 为什么现有的东西不行

| 痛点 | 现状 |
|---|---|
| **语音助手彻底失效** | Siri / Alexa 试图用低信息密度的声音"念经汇报"，交互机械、容错率低，在真实社交场合极其尴尬 |
| **上下文搬运成本** | 你和同事、客户聊了一整天，敲定了无数方案。回到电脑前面对 AI，却要花几百字重新交代"刚才发生了什么、我怎么想的、对方提了什么需求" |
| **灵感与承诺的磨损** | 口头达成的因果（因为 A 发生了，所以我承诺周五前交付 B）极易遗忘；传统备忘录输入阻力太大，等于没有 |

---

## 📐 三大不可妥协的核心哲学

<table>
<tr><td width="33%" valign="top">

### 原则一 · 绝对静默旁听
`Passive Ingestion Only`

不发声、不反馈、不推理应答。系统是**单向输入通道**，只转录、识别人格、抽取事实。

**严禁**任何主动插嘴、智能建议或系统弹窗。

</td><td width="33%" valign="top">

### 原则二 · 声纹绝对主权
`Voiceprint Sovereignty`

对外界的一切声音完全冷漠——别人说什么都只作为背景数据处理。

只有**主人的声纹**且下达了明确行动指令时，系统才被允许打破静默。

</td><td width="33%" valign="top">

### 原则三 · 零赘述上下文贯通
`Zero-Context Overhead`

无论通过声音在现实中触发，还是坐到电脑前敲键盘开新会话，AI 必须**默认掌握最近现实中发生的一切因果脉络**。

消灭"对 AI 补充背景"这个操作本身。

</td></tr>
</table>

---

## 🔄 三态模型

```
               ┌──────────────────────────┐
               │    现实生活多方交谈环境    │
               └─────────────┬────────────┘
                             │ (全天候静默收音)
                             ▼
               ┌──────────────────────────┐
               │     说话人分离与声纹核验    │
               └──────┬────────────┬──────┘
                      │            │
         [日常闲聊/他人声音]        │ [检测到主人声纹 + 明确执行动词]
                      │            │
                      ▼            ▼
        ┌───────────────────┐    ┌─────────────────────┐
        │ 状态 A: 静默因果沉淀 │    │ 状态 B: 声纹授权执行 │
        │ - 区分你和对方的话 │    │ - 调取最新因果图谱  │
        │ - 提炼因果图谱入库 │    │ - 自动完成复杂任务  │
        │ - 绝不发出声音     │    └─────────────────────┘
        └─────────┬─────────┘
                  │
                  │ (共享同一套因果图谱底座)
                  ▼
        ┌───────────────────┐
        │ 状态 C: 电脑工作贯通 │
        │ - 打开编辑器/新会话│
        │ - 隐式自动注入上下文│
        │ - 输入极短命令即开工│
        └───────────────────┘
```

### 当前 MVP 覆盖到哪里

| 状态 | MVP v0.1 | 说明 |
|---|:---:|---|
| **A · 静默因果沉淀** | ✅ **已实现** | 手机上传音频 → 转写 → 说话人归属 → 因果/决策/承诺抽取 → 入库 |
| **C · 电脑工作贯通** | ✅ **已实现** | `ss brief` / `ss inject` / MCP server，一键把上下文塞进 Cursor / Claude |
| **B · 声纹授权执行** | 🚧 **二期** | 声纹录入（enroll）与比对能力已就位，实时指令拦截与执行器在二期 |

> **为什么二期不做实时？** 实时语音 + 执行实现起来并不难，但**意义不大**。
> MVP 要验证的唯一命题是：*把一天的对话沉淀成记忆之后，电脑前的 AI 是否真的能少问我几句话。*
> 这个问题只需要异步管线就能回答，不需要实时。

---

## 🏗 架构

```
┌──────────────┐   HTTP multipart / chunked   ┌─────────────────────────────────────┐
│  手机录音端   │ ───────────────────────────▶ │        ShadowScribe Server          │
│ (你自己实现)  │                              │                                     │
└──────────────┘                              │  FastAPI  ──▶  SQLite 作业表         │
                                              │     │              │                │
                                              │     │              ▼                │
                                              │     │      ┌───────────────┐        │
                                              │     │      │   Worker      │        │
                                              │     │      │               │        │
                                              │     │      │ ffmpeg 归一化  │        │
                                              │     │      │      ↓        │        │
                                              │     │      │ faster-whisper│        │
                                              │     │      │      ↓        │        │
                                              │     │      │ 声纹说话人归属 │        │
                                              │     │      │      ↓        │        │
                                              │     │      │ LLM 因果蒸馏   │        │
                                              │     │      └───────┬───────┘        │
                                              │     │              ▼                │
                                              │     │      ┌───────────────┐        │
                                              │     └─────▶│  记忆底座      │        │
                                              │            │ causal-memory │        │
                                              │            │ (或 native)   │        │
                                              └───────────────────┬─────────────────┘
                                                                  │
                        ┌─────────────────────────────────────────┘
                        ▼
        ┌───────────────────────────────────────────┐
        │          电脑工作台（状态 C）               │
        │                                           │
        │  ss brief      打印上下文卡片 / 复制        │
        │  ss inject     写进编辑器（没有 MCP 时用）   │
        │  编辑器直连服务器的 /mcp 端点               │
        │                                           │
        │  ▼ 新会话直接输入：「写测试用例」            │
        │  ▼ AI 已经知道今天聊了什么、你承诺了什么     │
        └───────────────────────────────────────────┘
```

**记忆底座不是自己造轮子。** 因果图谱、海马体扩散激活、SWR 巩固、反事实回放这些能力来自
[`causal-memory`](https://github.com/JingxuanC/causal-memory)（MCP + PyO3 绑定）。
影书只负责它不擅长的部分：**从现实世界的声音里，把因果抠出来喂给它。**

```
现实世界的声音  ──[ 影书 ]──▶  结构化因果  ──[ causal-memory ]──▶  可检索、可推理的记忆
                (耳朵 + 蒸馏)                  (记忆 + 推理)
```

---

## ⚡ 快速开始

### 1. 起服务端（Docker，推荐）

```bash
git clone https://github.com/yw1103/ShadowScribe.git && cd ShadowScribe
cp .env.example .env
```

编辑 `.env`，**至少填这两项**：

```ini
SS_TOKEN=<运行 python -c "import secrets;print(secrets.token_urlsafe(32))" 生成>
SS_LLM_API_KEY=<你的 DeepSeek Key>
```

然后：

```bash
docker compose up -d --build
docker compose logs -f worker
```

验证（不需要手机就能跑通全链路）：

```bash
docker compose exec api shadowscribe doctor
docker compose exec api shadowscribe ingest /path/to/some.m4a --hint "与老王在会议室"
docker compose exec api shadowscribe brief
```

或者用仓库自带的端到端验证脚本，它会走**真实的手机接入协议**、顺便检查幂等去重、
然后打印转写、上下文卡片、承诺和检索结果：

```bash
SS_TOKEN=<你的token> ./scripts/verify_e2e.sh /path/to/some.m4a --hint "与老王在会议室"
```

没有现成音频？Windows 上可以合成一段（需要中文 SAPI 语音 + ffmpeg）：

```powershell
.\scripts\generate_demo_audio.ps1 -OutFile C:\tmp\meeting.m4a
```

这段合成对话刻意包含了 3 个承诺、2 个决策和 1 条教训型因果——
正确的抽取应该在每个类别都有产出。

### 2. 接上电脑端 —— 本机什么都不用装

**MCP 服务跑在服务器上**，所以你的电脑只需要一条配置。把下面这段贴进
`~/.cursor/mcp.json`（或 Cursor → Customize → MCP）：

```json
{
  "mcpServers": {
    "shadowscribe": {
      "url": "http://<你的服务器>:18080/mcp",
      "headers": { "Authorization": "Bearer <SS_TOKEN>" }
    }
  }
}
```

重启 Cursor，完事。**没有 pip install、没有本地进程、没有代理。**

> 这一步可以用脚本自动完成，顺便把下面那段静态指令也写好：
>
> ```powershell
> .\scripts\setup-client.cmd http://<你的服务器>:18080 <SS_TOKEN>   # Windows，可双击
> ./scripts/setup-client.sh --endpoint http://<你的服务器>:18080 --token <SS_TOKEN>
> ```

### 3. 用起来

在 Cursor / Claude 里**直接说事**，它会自己去调 `get_reality_context`：

> **你**：写测试用例
>
> **AI**：（自己拉了今天的现实上下文）…基于老王反馈的 Safari 白屏和周四的修复承诺，
> 我先写兼容性回归用例…

唯一需要手动一次的，是把下面这段贴进 **Cursor → Customize → Rules → User Rules**
（Cursor 唯一有文档保证的全局机制，没有公开的文件接口）：

```markdown
**开始处理任务前，先调用 `get_reality_context`。**
用户的指令通常很短、缺背景 —— 背景不在他脑子里等你追问，而在影书里。
**不要反问"你指的是什么"，先拉上下文。**
```

想亲眼看看最近发生了什么（不写任何文件）：

```bash
ss brief --copy     # 打印上下文卡片并复制到剪贴板，可贴进任何 AI
```

---

## 💡 它到底省了什么：一次真实对比

假设下午你和同事在会议室聊了一刻钟，回工位要写登录页的测试用例。

<table>
<tr><th width="50%">❌ 没有影书</th><th width="50%">✅ 有影书</th></tr>
<tr><td valign="top">

你在 Cursor 里新建会话，然后开始打：

> 我们刚才开会讨论了登录页的问题。老王说 Safari 上有白屏，用户投诉挺多的，他觉得这个优先级最高。我答应他周四之前给个修复方案。另外我们决定新功能先往后放，先把兼容性做完。还有结算模块上周没做回归测试就上线，结果对账金额错乱了，这个也得加测试。所以现在写测试用例，重点覆盖……

**约 140 字，且你已经记不清一半细节。**

</td><td valign="top">

新建会话，直接打：

> 写测试用例

因为 Cursor 已经在会话开始时自动读到了影书注入的上下文：

```markdown
## ⏳ 进行中的承诺
- [ ] 提交登录页 Safari 白屏修复方案 → 老王 · 截止 周四

## 🎯 关键决策
- 登录页先兼容 Safari，新功能排期后延（我拍的）
  — 依据：用户投诉集中

## 🔗 因果脉络
- 老王反馈 Safari 白屏用户投诉多 → 我承诺周四交修复方案 `登录页`
- 未做回归测试直接上线结算模块 → 线上对账金额错乱 `结算`

## 💬 原话锚点
- [14:20] 我: "周四之前我给你一个方案"
```

**0 字背景搬运。** 而且它比你记得更准。

</td></tr>
</table>

---

## 📱 手机端接入协议

手机端由使用者自行实现，服务端提供稳定的 HTTP 协议。
**完整规范见 [`docs/ingestion-api.md`](docs/ingestion-api.md)。**

最简形式（一次上传一段音频）：

```bash
curl -X POST http://<server>:18080/v1/ingest/audio \
  -H "Authorization: Bearer $SS_TOKEN" \
  -F "file=@recording.m4a" \
  -F "client_id=pixel-8" \
  -F "session_hint=与老王在会议室" \
  -F "recorded_at=2026-01-08T14:05:00+08:00"
```

大文件（几百 MB 的一整天录音）建议用**分片续传**：

```bash
# 1. 开一个上传会话
curl -X POST "http://<server>:18080/v1/uploads?filename=day.m4a&total_parts=20&client_id=pixel-8" \
  -H "Authorization: Bearer $SS_TOKEN"
#    → {"upload_id":"...", "received_parts":0}

# 2. 逐片上传（可断点续传，重复上传同一片会安全覆盖）
curl -X PUT "http://<server>:18080/v1/uploads/<upload_id>/parts/0" \
  -H "Authorization: Bearer $SS_TOKEN" \
  --data-binary @part0.bin

# 3. 合并并入库
curl -X POST "http://<server>:18080/v1/uploads/<upload_id>/complete" \
  -H "Authorization: Bearer $SS_TOKEN"
```

设计要点：

- **幂等**：相同字节重复上传会返回原 `recording_id` 并标记 `dedup: true`，手机端重试无副作用。
- **格式无所谓**：任何 ffmpeg 能解的容器（m4a / opus / aac / amr / wav / mp3 / webm）都可直接传。
- **手机端不需要聪明**：不需要做 VAD、不需要切片、不需要转码，全在服务端完成。
- **零干扰**：上传接口从不返回建议、提示或需要用户确认的内容。

---

## 🖥 电脑端使用

```bash
ss setup                        # 【跑一次】注册 MCP + 写静态指令 → 之后永久无感
ss doctor                       # 自检：配置 / 网络 / 鉴权 / 数据 / 编辑器 / MCP

ss brief                        # 手动看一眼：打印上下文卡片
ss brief --copy                 # 顺便复制到剪贴板（贴进任何 AI 都能用）
ss brief --hours 72             # 回溯 3 天
ss commitments                  # 我还欠着谁什么？
ss commitments --done <id>      # 勾掉一项
ss search 登录页                 # 搜记忆库 + 原始转写
ss timeline --day 2026-01-08    # 那天都发生了什么
ss upload meeting.m4a --hint "与老王在会议室"
ss status                       # 服务端健康与存量
```

### MCP：让编辑器自己来拉

在 Cursor / Claude Desktop / Claude Code 的 MCP 配置里加上：

```json
{
  "mcpServers": {
    "shadowscribe": { "command": "ss", "args": ["mcp"] }
  }
}
```

暴露的工具：`get_reality_context`、`list_open_commitments`、`search_reality`、
`get_timeline`、`pending_work_summary`，以及 `shadowscribe://brief` 资源。

每个工具的说明都写明了**什么时候该调用它**——这样才能让 AI 从"有这个工具"变成"自动继承上下文"。

---

## ⚙️ 关键配置

全部通过 `SS_*` 环境变量控制，完整带注释的清单见 [`.env.example`](.env.example)。

| 变量 | 默认 | 说明 |
|---|---|---|
| `SS_TOKEN` | *(空)* | 所有 `/v1` 接口的 Bearer Token。**公网部署必填** |
| `SS_LLM_API_KEY` | *(空)* | 不填则降级为"只存转写、不抽因果" |
| `SS_LLM_MODEL` | `deepseek-chat` | 任何 OpenAI 兼容端点均可 |
| `SS_WHISPER_MODEL` | `small` | `small` 首次启动快；`large-v3` 中文质量明显更好 |
| `SS_DIARIZATION` | `off` | 改 `embedding` 启用声纹「主人 / 对方」归属 |
| `SS_MEMORY_BACKEND` | `causal-memory` | 或 `native`（内置 SQLite，零额外依赖） |
| `SS_KEEP_AUDIO` | `true` | 设 `false` 则蒸馏完立即删除音频，只留文字 |
| `SS_TIMEZONE` | `Asia/Shanghai` | 决定"周四""下周一"换算成哪个日期 |

---

## 🔒 隐私与边界

这是一个**会把你全天对话送进服务器**的系统，所以边界必须说清楚：

- **MVP 阶段不做端侧加密。** 当前定位是个人内测，加密在这里是伪需求——它不解决任何
  真实痛点，只会拖慢验证。后续如果需要，接口层已预留改造空间。
- **服务端只做 Token 鉴权**，请务必设置 `SS_TOKEN` 并只在内网 / 隧道后暴露。
- **推荐 `SS_KEEP_AUDIO=false`**：蒸馏完成后自动删除音频，只保留转写与因果记忆。
- **不要把这套东西部署在任何你无法完全控制的机器上。**
- **合法合规**：请遵守你所在司法辖区关于录音与个人信息保护的法规。录制他人对话前
  请取得必要同意。本软件不为使用者的合规行为背书。

---

## 📁 项目结构

```
ShadowScribe/
├── server/                        # 服务端：耳朵 + 蒸馏
│   ├── shadowscribe/
│   │   ├── api.py                 # HTTP 接口（接入 / 检索 / 声纹）
│   │   ├── service.py             # 读模型 —— build_brief() 就是产品本身
│   │   ├── pipeline/
│   │   │   ├── audio.py           # ffmpeg 归一化为 16 kHz 单声道
│   │   │   ├── asr.py             # faster-whisper 转写
│   │   │   ├── diarize.py         # 声纹主人/对方归属
│   │   │   ├── distill.py         # LLM 抽取因果 / 决策 / 承诺
│   │   │   └── runner.py          # 全链路编排
│   │   ├── memory/                # 可插拔记忆底座
│   │   │   ├── causal_memory_backend.py
│   │   │   └── native.py          # 零依赖 SQLite 兜底
│   │   ├── worker.py              # 作业队列消费
│   │   └── cli.py                 # shadowscribe 运维命令
│   └── Dockerfile
├── client/                        # 电脑端：ss CLI（MCP 在服务端，本机零安装）
│   └── shadowscribe_client/
│       ├── cli.py
│       ├── inject.py              # 写进 Cursor / CLAUDE.md 等
│       └── mcp_server.py
├── docs/
│   ├── architecture.md
│   ├── ingestion-api.md           # 手机端接入规范
│   ├── desktop-integration.md
│   ├── memory-model.md
│   ├── deployment.md
│   └── roadmap.md
├── scripts/
│   ├── verify_e2e.sh              # 端到端验证（走真实接入协议）
│   ├── generate_demo_audio.ps1    # 合成中文会议音频（Windows）
│   └── demo_dialogue.zh.txt
├── docker-compose.yml
└── .env.example
```

---

## 🗺 路线图

- [x] **v0.1 — MVP**：异步管线 + 因果蒸馏 + 上下文卡片 + CLI/MCP
- [ ] **v0.2**：多说话人聚类（不止"主人/对方"）、承诺自动闭环（从后续对话里识别"已完成"）
- [ ] **v0.3**：端侧增量上传 + 本地 VAD 省流量；时间轴可视化
- [ ] **v0.4 — 状态 B**：常驻声纹监听 + 指令动词识别 + 执行器授权
- [ ] **v1.0**：端到端加密、多设备同步、记忆库可携带导出

详见 [`docs/roadmap.md`](docs/roadmap.md)。

---

## 🤝 参与贡献

欢迎 Issue 与 PR。请先读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

如果你在实现手机端，最需要反馈的是 [`docs/ingestion-api.md`](docs/ingestion-api.md)
——协议设计得够不够让手机端"不需要聪明"。

## 🙏 致谢

- [causal-memory](https://github.com/JingxuanC/causal-memory) — 因果记忆底座，本项目的记忆层
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2 上的 Whisper 推理
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) — 声纹嵌入
- [Model Context Protocol](https://modelcontextprotocol.io/) — 电脑端上下文贯通协议

## 📄 License

[MIT](LICENSE)
