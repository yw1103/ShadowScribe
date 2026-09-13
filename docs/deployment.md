# 部署

两种形态：**Docker Compose（推荐）** 和 **裸机 Python**。

本文以 Linux 服务器为例。参考环境：Ubuntu 22.04 / 40 vCPU / 62 GB RAM / 无 GPU /
Docker 29 + Compose v5。

---

## 1. Docker Compose（推荐）

### 1.1 准备

```bash
git clone https://github.com/yw1103/ShadowScribe.git
cd ShadowScribe
cp .env.example .env
```

**至少填这两项**：

```ini
SS_TOKEN=<生成一个>
SS_LLM_API_KEY=<DeepSeek key>
```

生成 token：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 1.2 启动

```bash
docker compose up -d --build
docker compose logs -f worker
```

首次构建会安装 `faster-whisper`（含 CTranslate2，约 40 MB）与 `causal-memory`（约 8 MB）。
国内机器建议保持 `.env` 里的 `PIP_INDEX_URL` 指向清华镜像。

### 1.3 验证

```bash
docker compose exec api shadowscribe doctor
```

期望输出：

```
[OK  ] ffmpeg              /usr/bin/ffmpeg
[OK  ] faster-whisper      importable
[OK  ] memory backend (causal-memory)  {'backend': 'causal-memory', ...}
[OK  ] LLM distillation    deepseek-chat @ https://api.deepseek.com/v1
[OK  ] speaker embedding   ready
[OK  ] data dir writable   /data

6/6 checks passed
```

`speaker embedding` 是 MISS 的话看第 4 节；它不影响转写和蒸馏，只影响「主人/对方」归属。

**确认 MCP 端点活着**（电脑端连的就是它）：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/mcp   # 期望 200
```

`404` = 镜像里没装 `mcp`。检查 `.env` 的 `INSTALL_MCP=true` 后重建。

**不用手机就能跑通全链路**：

```bash
docker compose cp ./some-meeting.m4a api:/tmp/
docker compose exec api shadowscribe ingest /tmp/some-meeting.m4a --hint "与老王在会议室"
docker compose exec api shadowscribe brief
```

---

## 2. 首次运行会发生什么

第一次处理音频时，worker 会从 `SS_HF_ENDPOINT`（默认 `hf-mirror.com`）下载
Whisper 模型到 `/data/models`，**这是一次性的**，之后完全离线可用。

| 模型 | 体积 | 中文质量 | 相对速度（40 核 CPU） |
|---|---|---|---|
| `tiny` | 75 MB | 差 | 很快 |
| `base` | 145 MB | 一般 | 快 |
| `small` | 480 MB | 可用（默认） | 约 0.3–0.5× 音频时长 |
| `medium` | 1.5 GB | 好 | 约 1–1.5× 音频时长 |
| `large-v3` | 3.0 GB | 最好 | 约 3–4× 音频时长 |

"0.5× 音频时长"意思是：10 分钟录音约 5 分钟处理完。

**建议**：先用 `small` 把链路跑通看效果，满意后改成 `large-v3` 长期使用：

```bash
sed -i 's/^SS_WHISPER_MODEL=.*/SS_WHISPER_MODEL=large-v3/' .env
docker compose up -d --force-recreate worker
```

> 磁盘：`large-v3` + 一天的原始音频（Opus 32 kbps 约 300 MB）+ 归一化 WAV
> （16 kHz 单声道约 115 MB/小时）——按每周 40 小时录音估算，建议预留 **50 GB 以上**。
> WAV 可以用 `SS_KEEP_AUDIO=false` 自动清理。

---

## 3. 对外暴露

服务端监听 `0.0.0.0:18080`，对外提供两条路径，安全等级不同：

| 路径 | 用途 | 鉴权 |
|---|---|---|
| `/v1/*` | 手机上传、查询、声纹录入 | **需要 `SS_TOKEN`** |
| `/mcp` | 编辑器拉取上下文 | 默认**不需要**（单人自用），`SS_MCP_REQUIRE_TOKEN=true` 可打开 |

> `/mcp` 默认开放是有意的：单人自用，编辑器配置里少一个 header。
> **但它会返回全部转写和承诺** —— 这台机器不只你用、或者端口暴露面变大的时候，
> 先把它设成 `true`。

三种暴露方式：

### 反向代理 + HTTPS（推荐）

```nginx
location / {
    proxy_pass http://127.0.0.1:18080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    client_max_body_size 2048m;      # 必须 ≥ SS_MAX_UPLOAD_MB
    proxy_read_timeout 3600s;        # 大文件上传

    # MCP 走 Streamable HTTP（含 SSE），必须关掉缓冲，否则握手会挂住
    proxy_buffering off;
    proxy_set_header Connection '';
    proxy_http_version 1.1;
}
```

### SSH 隧道（最快，适合临时用）

```bash
ssh -N -L 18080:127.0.0.1:18080 root@<server>
# 然后本地：ss login --endpoint http://127.0.0.1:18080 --token <SS_TOKEN>
```

### 内网穿透 / frp

本项目的参考部署使用 frp 把内网 `22` 映射到公网 `16780`。
给 18080 开一条同样的映射即可，然后：

```bash
ss login --endpoint http://<公网IP>:<公网端口> --token <SS_TOKEN>
# 编辑器 MCP 配置里也填这个地址 + /mcp
```

> ⚠️ 内网穿透通常**不带 TLS**。Token 与音频会以明文经过中间节点。
> 长期使用请改用反向代理 + HTTPS，或走 VPN。

---

## 4. 声纹（参考部署已启用）

启用后转写会带 `speaker: owner | guest`，因果归属准确度显著提升。

```bash
# 1. 重建镜像时带上 sherpa-onnx
INSTALL_SPEAKERS=true docker compose build

# 2. 下载中文说话人嵌入模型（走 hf-mirror：实测 5 MB/s，GitHub release 只有 65 KB/s）
docker run --rm -v shadowscribe-data:/data alpine sh -c '
  mkdir -p /data/models/speaker && cd /data/models/speaker &&
  curl -fL -o campplus.onnx \
    https://hf-mirror.com/csukuangfj/speaker-embedding-models/resolve/main/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx'

# 3. 打开开关
cat >> .env <<'EOF'
INSTALL_SPEAKERS=true
SS_DIARIZATION=embedding
SS_SPEAKER_MODEL_DIR=/data/models/speaker
SS_OWNER_THRESHOLD=0.55
EOF
docker compose build && docker compose up -d --force-recreate
```

> 模型放在**数据卷里**，跟着卷一起迁移，换机器不用重下。
> 任何 16 kHz 说话人嵌入 ONNX 放在 `SS_SPEAKER_MODEL_DIR` 或
> `/data/models/speaker/*.onnx` 都会被自动识别，文件名不重要。

录入主人声纹（**15–60 秒单独说话**）：

```bash
curl -X POST http://<server>:18080/v1/speakers/enroll \
  -H "Authorization: Bearer $SS_TOKEN" \
  -F "file=@my_voice.m4a" -F "label=主人"
```

**录入后要把已有录音重跑一遍**才会带上标签：

```bash
for id in $(curl -s -H "Authorization: Bearer $SS_TOKEN" \
            "http://<server>:18080/v1/recordings?status=done&limit=100" \
            | python3 -c "import sys,json;[print(r['id']) for r in json.load(sys.stdin)['recordings']]"); do
  curl -s -X POST -H "Authorization: Bearer $SS_TOKEN" \
    "http://<server>:18080/v1/recordings/$id/reprocess" > /dev/null && echo "requeued $id"
done
```

> **worker 会自动读到新声纹**，不用重启容器 —— 它是按声纹的 id + created_at
> 判断是否变化的。曾经有个版本把它缓存死在进程里，重跑完全没效果。

---

## 5. 运维

```bash
docker compose ps
docker compose logs -f --tail 100 worker
docker compose restart worker

# 处理积压
docker compose exec api shadowscribe stats

# 清理过期音频（默认保留 30 天）
docker compose exec api shadowscribe prune --days 30

# 更新到最新版
git pull && docker compose up -d --build

# 备份（整个 /data 卷就是全部状态）
docker run --rm -v shadowscribe-data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/shadowscribe-$(date +%F).tar.gz -C /data .
```

### 健康检查

```bash
curl -s http://127.0.0.1:18080/healthz                       # 免认证
curl -s -H "Authorization: Bearer $SS_TOKEN" \
     http://127.0.0.1:18080/v1/health                        # 带统计
```

### 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| worker 日志 `ffmpeg not found` | 用了裸机部署但没装 ffmpeg | `apt-get install -y ffmpeg` |
| 模型下载卡住 | `hf-mirror` 不可达 | 换 `SS_HF_ENDPOINT`，或手动把模型放进 `/data/models` |
| 作业一直 `queued` | worker 容器没起来 | `docker compose ps`；看 worker 日志 |
| 作业 `failed: AsrError` | 模型加载失败 / 磁盘满 | `shadowscribe doctor`；`df -h` |
| 上下文卡片为空 | 没配 LLM key，或录音没处理完 | `ss recordings` 看状态 |
| `causal-memory` 降级 | wheel 没装成功（架构不匹配） | `docker compose exec api python -c "import causal_memory"` |
| 上传 `413` | 超过 `SS_MAX_UPLOAD_MB` | 调大，或用分片上传 |

---

## 6. 裸机部署（不用 Docker）

```bash
sudo apt-get install -y ffmpeg python3-venv python3-dev build-essential

git clone https://github.com/yw1103/ShadowScribe.git && cd ShadowScribe/server
python3 -m venv .venv && . .venv/bin/activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e '.[memory,dev]'

cp ../.env.example .env && $EDITOR .env
shadowscribe doctor
```

systemd 单元：

```ini
# /etc/systemd/system/shadowscribe-api.service
[Unit]
Description=ShadowScribe API
After=network.target

[Service]
Type=simple
User=shadow
WorkingDirectory=/opt/shadowscribe/server
EnvironmentFile=/opt/shadowscribe/server/.env
ExecStart=/opt/shadowscribe/server/.venv/bin/shadowscribe serve
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

worker 单元同理，把 `ExecStart` 换成 `.../shadowscribe-worker`。

```bash
sudo systemctl enable --now shadowscribe-api shadowscribe-worker
```

> 容器镜像里 `SS_DATA_DIR=/data`。裸机部署请显式设置成绝对路径，例如
> `SS_DATA_DIR=/var/lib/shadowscribe`，否则会落到当前工作目录。
