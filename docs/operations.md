# 影书 ShadowScribe 操作手册

> 这份文档回答一个问题：**我（使用者）每天到底要做什么。**
> 架构与设计取舍见 [`architecture.md`](architecture.md)；部署与运维见 [`deployment.md`](deployment.md)。

---

## 0. 系统全景

```
   ┌─────────────┐        ┌──────────────────────────────┐        ┌──────────────┐
   │   手机       │        │   服务器                      │        │   电脑        │
   │             │        │                              │        │              │
   │ 录音        │  HTTP  │  内网穿透 ──▶ 18080          │  HTTP  │  ss CLI      │
   │ 上传        │ ─────▶ │                              │ ◀───── │  ss mcp      │
   │             │        │  api 容器 + worker 容器       │        │              │
   │ 你来实现     │        │  共享 /data 卷                │        │  你只需要用   │
   └─────────────┘        │  ├ SQLite（录音/转写/承诺）    │        └──────────────┘
                          │  ├ 音频文件                   │
                          │  ├ Whisper 模型               │
                          │  └ causal-memory 因果图谱      │
                          └──────────────────────────────┘
```

**三个角色的分工：**

| 角色 | 负责 | 不负责 |
|---|---|---|
| **手机** | 录音、上传、失败重传 | 不做 VAD、不切片、不转码、不做任何判断 |
| **服务器** | 转写、说话人归属、因果抽取、记忆存储 | 不发声、不通知、不主动打扰 |
| **电脑** | 拉取上下文、注入编辑器 | 不需要手写背景说明 |

**数据只有一份**：全在服务器 `/data` 卷里。手机和电脑都是无状态的客户端。

---

## 1. 一次性准备

### 1.1 确认服务器在跑

```bash
ssh <你的服务器>            # 本项目参考部署：ssh shadowscribe
cd /opt/shadowscribe/app

docker compose ps           # api 和 worker 都应该是 Up
curl -s http://127.0.0.1:18080/healthz
docker compose exec api shadowscribe doctor
```

`doctor` 应该输出 6/6 全通过：

```
[OK  ] ffmpeg              /usr/bin/ffmpeg
[OK  ] faster-whisper      importable
[OK  ] memory backend (causal-memory)  ...
[OK  ] LLM distillation    deepseek-chat @ https://api.deepseek.com/v1
[OK  ] speaker embedding   ready
[OK  ] data dir writable   /data
```

**拿到两样东西**，电脑端要用：

```bash
# 1) 外网地址（内网穿透给你的）
#    本项目参考部署：http://47.102.212.49:18080

# 2) SS_TOKEN
docker compose exec -T api printenv SS_TOKEN
```

> ⚠️ `SS_TOKEN` 是唯一凭据。**不要提交到 git，不要发到公开场合。**

---

### 1.2 录入声纹 ← 决定因果归属准不准

**为什么必须做**：不录入声纹时，转写里每一句的说话人都是「未知」，
抽取器只能靠措辞猜「这句话是我说的还是对方说的」。猜错一次，
系统就会把你的承诺记成别人的、或者把别人欠你的记成你欠别人的——
这是整个系统里**代价最高的错误**。

录入之后，每一句都会带上 `owner`（我）/ `guest`（对方）标签，归属变成确定性的。

#### 需要什么音频

| 要求 | 说明 |
|---|---|
| 时长 | **15–60 秒** |
| 内容 | **你一个人**连续说话（读一段文章即可，不要有别人插话） |
| 环境 | 尽量安静，用你平时录音的那台手机录最好 |
| 格式 | 任意 ffmpeg 能解的容器：m4a / opus / wav / mp3 / aac |

#### 录入命令

```bash
curl -X POST http://<服务器地址>/v1/speakers/enroll \
  -H "Authorization: Bearer <SS_TOKEN>" \
  -F "file=@我的声音.m4a" \
  -F "label=主人"
```

成功返回：

```json
{ "speaker_id": "…", "label": "主人", "dim": 192, "sample_seconds": 23.4 }
```

#### 常见错误

| 返回 | 含义 | 怎么办 |
|---|---|---|
| `503` + `ffmpeg is not available` | 服务器没装 ffmpeg | 用官方镜像部署，或 `apt install ffmpeg` |
| `503` + `speaker embedding model unavailable` | 服务器没有声纹模型 | 见下方「启用声纹」 |
| `422` + `could not extract a voiceprint` | 音频太短/太吵/没人声 | 重录，至少 15 秒连续说话 |
| `401` | token 不对 | `docker compose exec -T api printenv SS_TOKEN` |

#### 启用声纹（服务器侧，只需一次）

参考部署已经开好了。若要自己开：

```bash
cd /opt/shadowscribe/app

# 1) 下载中文声纹模型（走 hf-mirror，实测 5 MB/s，GitHub release 只有 65 KB/s）
docker run --rm -v shadowscribe-data:/data alpine sh -c '
  mkdir -p /data/models/speaker && cd /data/models/speaker &&
  curl -fL -o campplus.onnx \
    https://hf-mirror.com/csukuangfj/speaker-embedding-models/resolve/main/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx'

# 2) 打开开关
cat >> .env <<'EOF'
INSTALL_SPEAKERS=true
SS_DIARIZATION=embedding
SS_SPEAKER_MODEL_DIR=/data/models/speaker
SS_OWNER_THRESHOLD=0.55
EOF

# 3) 重建（会把 sherpa-onnx 打进镜像）
docker compose build && docker compose up -d --force-recreate
```

#### 验证声纹生效

```bash
docker compose exec api shadowscribe doctor | grep speaker
curl -s -H "Authorization: Bearer <SS_TOKEN>" http://127.0.0.1:18080/v1/speakers
```

录入之后，**把之前的录音重新处理一遍**才会带上说话人标签：

```bash
# 对所有已完成的录音重新跑管线（默认不会重复写记忆，安全）
for id in $(curl -s -H "Authorization: Bearer <SS_TOKEN>" \
            "http://127.0.0.1:18080/v1/recordings?status=done&limit=100" \
            | python3 -c "import sys,json;[print(r['id']) for r in json.load(sys.stdin)['recordings']]"); do
  curl -s -X POST -H "Authorization: Bearer <SS_TOKEN>" \
    "http://127.0.0.1:18080/v1/recordings/$id/reprocess" > /dev/null
  echo "requeued $id"
done
```

> `SS_OWNER_THRESHOLD` 是余弦相似度阈值。**归属偏严**（很多句子被判成对方）就调低，
> 比如 `0.45`；**归属偏松**（别人的话被判成你）就调高，比如 `0.65`。

---

### 1.3 电脑端安装（一条命令）

> 客户端**尚未发布到 PyPI**。安装脚本会依次尝试 **PyPI → 本地仓库 → GitHub**，
> 所以不管哪种情况都能装上，你不需要关心走的是哪条。

#### Windows：双击就行

打开仓库里的 `scripts` 文件夹，**双击 `setup-client.cmd`**，按提示粘贴地址和 token。

> 为什么不是双击 `.ps1`？Windows 默认不把 `.ps1` 关联到 PowerShell（双击会弹
> 「选择打开方式」），而且默认禁止运行未签名脚本。这是系统设计，所以提供了一个
> `.cmd` 启动器来绕开这两件事 —— 它会自动用 `-ExecutionPolicy Bypass` 调用，
> 并在结束时暂停让你看清输出。

想在终端里跑：

```powershell
cd ShadowScribe\scripts
.\setup-client.cmd http://47.102.212.49:18080 <SS_TOKEN>
```

（也可以 `.\setup-client.ps1 -Endpoint ... -Token ...`，但要先确保执行策略允许。）

#### macOS / Linux

```bash
./scripts/setup-client.sh --endpoint http://47.102.212.49:18080 --token <SS_TOKEN>
```

脚本会依次做六件事，每步都打印结果：

1. 检查 Python ≥ 3.10
2. 安装 `shadowscribe-client[mcp]`（三个来源依次兜底）
3. 定位 `ss` 命令（不在 PATH 会告诉你怎么加）
4. 写入连接配置到 `~/.shadowscribe/config.json`
5. 运行 `ss doctor` 自检
6. **接线**：注册 MCP + 写永不变化的静态指令

第 6 步是「无感」的关键：跑完它之后，你就再也不需要运行任何影书命令。
细节见 [§2.2](#22-电脑端--你其实什么都不用做)。

#### 手工安装（如果不想用脚本）

```bash
pip install ".\client[mcp]"      # 在仓库目录内
ss login --endpoint http://47.102.212.49:18080 --token <SS_TOKEN>
ss doctor
```

`ss doctor` 长这样，任何一项 MISS 都会附上可直接复制的修复命令：

```
[OK  ] config      C:\Users\you\.shadowscribe\config.json → http://47.102.212.49:18080
[OK  ] token       已设置（43 字符）
[OK  ] 网络可达      http://47.102.212.49:18080/healthz  93 ms
[OK  ] 鉴权         2 段已处理 · 0 排队 · 0 失败 · 6 项待办
[OK  ] 记忆后端      causal-memory
[OK  ] 最近 24h 上下文  有内容
[OK  ] 承诺接口      正常
[OK  ] 本目录编辑器    GitHub Copilot
[OK  ] MCP 依赖      已安装（`ss mcp` 可用）

9/9 项通过
```

---

## 2. 日常使用

### 2.1 手机端（你要实现的部分）

**只需要实现一件事**：把录好的音频 POST 上去。完整协议见 [`ingestion-api.md`](ingestion-api.md)。

最小实现：

```
POST http://47.102.212.49:18080/v1/ingest/audio
Authorization: Bearer <SS_TOKEN>
Content-Type: multipart/form-data

file          = 音频文件（m4a / opus / aac / wav / amr 都行）
client_id     = 设备标识，如 pixel-8
session_hint  = 与老王、张总在会议室，主题：登录页兼容性   ← 强烈建议填
recorded_at   = 2026-01-08T14:05:00+08:00                ← 强烈建议填
```

`session_hint` 会被写进记忆的上下文，直接影响电脑端检索质量。
`recorded_at` 决定「周四前」这类相对时间换算成哪个日期。

**三条实现原则：**

1. **上传是后台任务，不是录音的一部分。** 录音绝不因为上传失败而中断。
2. **本地存待传队列**，有网就传，没网就攒着。
3. **不确定就重传**——服务端按内容 SHA-256 去重，重复上传无副作用。

音频建议 **Opus 24–32 kbps 单声道**，一天约 250–350 MB。
不要用 128 kbps 立体声，那是 10 倍流量且对识别毫无帮助。

### 2.2 电脑端 —— 你其实什么都不用做

装完之后，**在 Cursor 里直接说事就行**。它会自己去调 MCP 拿实时上下文。

下面说明为什么，以及两个可选动作。

#### 无感是怎么做到的

有两件**完全不同**的事，混起来就会得到一个每天要手动维护的系统：

| | **静态指令 + MCP**（默认） | **快照**（应急） |
|---|---|---|
| 命令 | `ss setup` **跑一次** | `ss inject` 每次重跑 |
| 内容 | "去调 MCP 拿上下文"这句话 | 当时那一刻的上下文卡片 |
| 新鲜度 | **实时** | 停在执行命令那一刻 |
| 你要做的 | **什么都不做** | 记得手动刷新 |

`ss setup` 写的是**左边这一列**：一段永不变化的指令，让编辑器自己在需要时拉数据。
所以它跑一次就够了 —— 没有任何东西会过期。

> 我曾经把右边的「快照」当成日常命令推荐，那是错的：快照一过期就要重跑，
> 等于让你每天手动搬运自己的上下文，而这正是影书要消灭的事。

#### 验证接线是否生效

```bash
ss doctor          # 看 MCP 依赖那一行
```

然后在 Cursor 里新开一个会话，问一句 **"我今天答应了谁什么"**。
如果它开始调 `get_reality_context` / `list_open_commitments`，就通了。

没通的话，检查 Cursor → Customize → Rules 里有没有 `shadowscribe`，
以及 Settings → MCP 里 `shadowscribe` 是不是绿的。

#### 可选动作一：手动看一眼（不写任何文件）

```bash
ss brief --copy        # 打印最近 24 小时上下文卡片，并复制到剪贴板
```

粘进任何 AI 对话框（包括网页版 ChatGPT）。卡片长这样：

```markdown
# 影书 · 现实上下文（2026-09-11 13:33 → 09-12 13:33）
**概览**：2 段录音 · 2 个话题片段 · 6 项待办 · 7 条因果。

## ⏳ 进行中的承诺
- [ ] 跟财务确认预付款比例后给客户答复 → 李经理 · 截止 09-13
- [ ] 给出完整的修复方案 → 老王 · 截止 09-17

## 🎯 关键决策
- 登录页Safari兼容性问题优先级最高，新功能往后放（我拍的）

## 🔗 因果脉络
- 客户提出预付款比例能否提到40% → 我方表示需跟财务确认，一般首付是30%

## 💬 原话锚点
[00:40] 未知: “这样,我周四之前给你一个明确的交付计划,包含里程碑和风险点。”
```

#### 可选动作二：快照注入（给没有 MCP 的客户端）

```bash
ss inject --auto
```

这个写的是**快照** —— 把当前卡片塞进编辑器的规则文件。适用于：

- 你用的客户端不支持 MCP
- 你想把上下文固化成文件留在仓库里

| 编辑器 | 写入文件 |
|---|---|
| Cursor | `.cursor/rules/shadowscribe.mdc` |
| Claude Code / Desktop | `CLAUDE.md` |
| Codex / Amp 等 | `AGENTS.md` |
| GitHub Copilot | `.github/copilot-instructions.md` |
| Windsurf | `.windsurfrules` |
| Cline / Roo | `.clinerules` |
| Gemini CLI | `GEMINI.md` |

**幂等**：内容包在 `<!-- SHADOWSCRIBE:BEGIN -->` / `END` 之间，重复执行是原地替换，
不会追加第二份。**标记之外的内容永远不动**（你手写的规则不会被覆盖）。
撤销用 `ss inject --remove`，预览用 `ss inject --dry-run`。

> ⚠️ 快照会过期。块里写了提醒，免得读到的人把一周前的卡片当成当前状态。
> 能用 MCP 就别用这条。

#### MCP 暴露了什么

`ss setup` 已经注册好了。手工注册的话：

```json
{
  "mcpServers": {
    "shadowscribe": {
      "url": "http://<服务器>:18080/mcp",
      "headers": { "Authorization": "Bearer <SS_TOKEN>" }
    }
  }
}
```

暴露 5 个工具 + 2 个资源：

| 工具 | 什么时候会被调用 |
|---|---|
| `get_reality_context` | 用户给出简短、缺背景的指令时（核心） |
| `pending_work_summary` | 新会话开场 |
| `list_open_commitments` | "我还欠什么"、排优先级、写周报 |
| `search_reality` | 提到具体的人/项目/事件，要确认"当初怎么说的" |
| `get_timeline` | "今天/昨天下午干了什么" |
| `shadowscribe://brief` | 资源，可被客户端自动附加 |
| `shadowscribe://commitments` | 资源 |

配置好之后，新会话直接打「写测试用例」，AI 会自己调用 `get_reality_context`。

### 2.3 全部命令

```bash
ss setup                                   # 【跑一次】注册 MCP + 写静态指令
ss doctor                                  # 自检（出问题先跑这个）
ss status                                  # 服务端存量与健康
ss brief [--hours N] [--copy] [--out F]    # 手动看一眼上下文卡片
ss commitments [--status open|done|all]    # 我还欠谁什么
ss commitments --done <id>                 # 勾掉一项
ss search <关键词>                          # 搜记忆库 + 原始转写
ss timeline [--day YYYY-MM-DD]             # 某天的时间轴
ss recordings [--status failed]            # 上传/处理状态
ss upload <文件> [--hint "..."]             # 从电脑上传一段音频（测试用）
ss inject [--auto|--target X] [--remove]   # 写【快照】进编辑器（没有 MCP 时才用）
ss mcp                                     # 启动 MCP 服务（`ss setup` 注册的就是它）
ss login --endpoint URL --token T          # 保存连接信息
```

---

## 3. 接口速查

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/v1/ingest/audio` | **手机上传**（multipart） |
| `POST` | `/v1/ingest/raw` | 裸 body 上传 |
| `POST` | `/v1/uploads` | 开分片上传会话（大文件） |
| `PUT` | `/v1/uploads/{id}/parts/{n}` | 上传第 n 片（可重传） |
| `POST` | `/v1/uploads/{id}/complete` | 合并入库 |
| `POST` | `/v1/speakers/enroll` | **录入声纹** |
| `GET` | `/v1/speakers` | 已录入的声纹 |
| `GET` | `/v1/context/brief` | **上下文卡片**（markdown） |
| `GET` | `/v1/commitments` | 承诺列表 |
| `PATCH` | `/v1/commitments/{id}?status=done` | 标记完成 |
| `GET` | `/v1/timeline?day=` | 时间轴 |
| `GET` | `/v1/search?q=` | 检索 |
| `GET` | `/v1/recordings` | 录音与状态 |
| `GET` | `/v1/recordings/{id}` | 详情（含转写） |
| `POST` | `/v1/recordings/{id}/reprocess` | 重新处理 |
| `DELETE` | `/v1/recordings/{id}` | 删除录音 |
| `GET` | `/v1/jobs/{id}` | 任务进度 |
| `GET` | `/v1/health` | 服务端状态（需 token） |
| `GET` | `/healthz` | 存活探针（**免 token**） |

除 `/healthz` 外全部需要 `Authorization: Bearer <SS_TOKEN>`。

---

## 4. 配置速查

服务器 `/opt/shadowscribe/app/.env`，改完 `docker compose up -d`。

| 变量 | 默认 | 什么时候改 |
|---|---|---|
| `SS_TOKEN` | — | 部署时生成，别改（改了电脑端要重新登录） |
| `SS_LLM_API_KEY` | — | 必填，否则只存转写不抽因果 |
| `SS_WHISPER_MODEL` | `small` | 中文长期用改 `large-v3`（质量明显更好，慢 3–4 倍） |
| `SS_DIARIZATION` | `embedding` | `off` 关闭声纹（归属退回靠措辞猜） |
| `SS_OWNER_THRESHOLD` | `0.55` | 归属偏严调低，偏松调高 |
| `SS_KEEP_AUDIO` | `true` | 设 `false`：蒸馏完立即删音频，只留文字 |
| `SS_AUDIO_RETENTION_DAYS` | `30` | `shadowscribe prune` 的保留期 |
| `SS_TIMEZONE` | `Asia/Shanghai` | 决定「周四」换算成哪天 |
| `SS_WHISPER_INITIAL_PROMPT` | 空 | 填你的专有名词，提升识别率，如 `影书、MCP、Cursor、登录页` |
| `SS_MAX_UPLOAD_MB` | `2048` | 手机单次上传上限 |

---

## 5. 排障

**先跑 `ss doctor`**，它会指出具体哪一环断了并给出修复命令。

| 现象 | 原因 | 处理 |
|---|---|---|
| `cannot reach …` | 服务端没起 / 端口没开 / 穿透规则失效 | `curl <地址>/healthz`；服务器上 `docker compose ps` |
| `401 Unauthorized` | token 不对 | `docker compose exec -T api printenv SS_TOKEN` 后重新 `ss login` |
| 卡片说「没有已处理的录音」 | 窗口内没有数据 | 按卡片提示调大 `--hours`；或看 `ss recordings` 是否还在排队 |
| 有录音但 `status: failed` | 看错误详情 | `ss recordings --status failed`；多半是模型下载失败或磁盘满 |
| 归属记反 | 声纹没开 / 阈值不合适 | 见 §1.2；`SS_OWNER_THRESHOLD` 微调 |
| `ss brief` 输出乱码 | 老式 Windows 控制台编码 | 已在 v0.1 修复；`pip install -U ".\client"` 升级客户端 |
| 检索重复 | 记忆是追加写的，重跑会产生重复边 | 已知限制，见 [`roadmap.md`](roadmap.md) 的「写边幂等」 |
| MCP 工具调用报错 | `mcp` 包版本 | `pip install -U ".\client[mcp]"`（已同时支持 mcp 1.x 与 2.x） |
| 双击 `.ps1` 弹出「选择打开方式」 | Windows 不把 `.ps1` 关联到 PowerShell | 双击 `setup-client.cmd`；这是系统设计，不是脚本坏了 |
| `setup-client.ps1` 报语法错误 / 中文乱码 | 脚本丢了 UTF-8 BOM，PS 5.1 按 GBK 读 | 用 `.cmd` 启动器；仓库有测试守住这一点 |

### 看日志

```bash
cd /opt/shadowscribe/app
docker compose logs -f worker       # 处理管线
docker compose logs -f api          # 接口
docker compose logs --tail 200 worker
```

### 备份

整个状态就是 `/data` 卷：

```bash
docker run --rm -v shadowscribe-data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/shadowscribe-$(date +%F).tar.gz -C /data .
```

---

## 6. 后续：迁移到正式服务器

现在的形态是**临时**的：一台机器同时跑着别的服务，通过内网穿透对外。
迁移到正式服务器时：

1. **新服务器**：`git clone` → `cp .env.example .env` → 填 `SS_TOKEN` / `SS_LLM_API_KEY`
   → `docker compose up -d --build`
2. **搬数据**：把旧机器的 `/data` 卷打包，在新机器上还原（整卷就是全部状态）
3. **搬模型**：`/data/models` 里已有 Whisper 缓存和声纹模型，跟着卷一起走，不用重下
4. **换地址**：电脑端 `ss login --endpoint http://新地址:18080 --token <同一个 SS_TOKEN>`
5. **手机端**：只改 base URL
6. **去掉穿透**：正式服务器建议用 **反向代理 + HTTPS**（见 [`deployment.md`](deployment.md) §3），
   不要长期跑明文穿透

因为 `SS_TOKEN` 和因果记忆库都在数据卷里，迁移过程中**电脑端和手机端的历史不会丢**，
换的只是 base URL。

> 提醒：迁移后记得把旧机器的穿透规则关掉，避免两个实例同时对外。

---

## 7. 你每天需要记的命令：零条

```bash
ss setup             # 只在装完/换电脑时跑一次，之后永远不用再跑
```

之后在 Cursor 里直接说事。它会自己去拉现实上下文 —— **你不需要在"现实"和"AI"之间当搬运工**。

唯一可选的例外：想亲眼看看最近发生了什么，或者要贴进网页版 ChatGPT：

```bash
ss brief --copy
```
