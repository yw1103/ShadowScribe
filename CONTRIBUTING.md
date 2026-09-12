# 参与贡献

感谢你有兴趣。这个项目目前是 **v0.1 MVP**，最有价值的贡献是**指出它哪里没用**。

---

## 最有价值的反馈

在提功能之前，先问这三个问题：

1. **它真的减少了你的背景搬运吗？** 如果 `ss brief` 输出的卡片你还是要手动改一遍
   才能用，那是抽取质量问题，请把**原始对话**和**生成的卡片**一起贴出来。
2. **手机端接入协议够不够"不需要聪明"？** 见 [`docs/ingestion-api.md`](docs/ingestion-api.md)。
   如果你实现手机端时发现需要做协议没写的事，那是一个 bug。
3. **因果有没有记反？** `actor` 搞错（把对方的话记成你的承诺）是最严重的错误。
   这类 issue 优先级最高。

---

## 开发环境

```bash
git clone https://github.com/yw1103/ShadowScribe.git
cd ShadowScribe

# 服务端
cd server
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e '.[memory,speakers,dev]'

# 电脑端
cd ../client
pip install -e '.[mcp,dev]'
```

需要 `ffmpeg` 在 PATH 上：`apt-get install -y ffmpeg`（或 `brew install ffmpeg`）。

### 不装重依赖也能跑测试

大部分测试**不依赖** ffmpeg / Whisper / LLM：

```bash
cd server && pytest              # 单元测试
cd client && pytest
```

需要真实模型的端到端测试标记为 `slow`：

```bash
pytest -m "not slow"             # 默认
pytest -m slow                   # 需要 ffmpeg + 模型 + LLM key
```

---

## 代码约定

| 项 | 约定 |
|---|---|
| Python | 3.10+，`from __future__ import annotations`，行宽 100 |
| 格式化 / lint | `ruff check` + `ruff format`（配置在各 `pyproject.toml`） |
| 类型 | 公开函数必须有类型标注；`mypy` 不强制但别倒退 |
| 注释 | **解释为什么，不解释是什么。** 见下方 |
| Commit | [Conventional Commits](https://www.conventionalcommits.org/)：`feat:` `fix:` `docs:` `refactor:` `test:` `chore:` |
| 分支 | `main` 保持可运行；改动走 feature 分支 + PR |

### 注释风格

这个项目的注释密度**高于典型 Python 项目**，这是有意的。要求：

- 每个模块顶部一段话说明**它为什么存在**，而不是它包含什么。
- 在"本来可以更简单但故意没这么做"的地方写清楚取舍。
- 不要写 `# 遍历列表` 这种把代码翻译一遍的注释。

反例（不要）：

```python
# 遍历所有 segments
for seg in segments:
```

正例：

```python
# 传 labels=None 而不是空列表：蒸馏器会把 None 理解为"没有声纹信息，
# 请从措辞推断"，而空列表会让它以为每句话都属于未知说话人。
labels=None if self.s.diarization == "off" else labels,
```

### 文档同步

改代码时同步改文档。特别是：

- 加了 `SS_*` 配置 → 更新 [`.env.example`](.env.example) 和 README 配置表
- 改了接口 → 更新 [`docs/ingestion-api.md`](docs/ingestion-api.md)
- 改了数据结构 → 更新 [`docs/memory-model.md`](docs/memory-model.md)
- 完成了路线图条目 → 更新 [`docs/roadmap.md`](docs/roadmap.md)

---

## 提交前自检

```bash
ruff check . && ruff format --check .
pytest
```

PR 描述里请包含：

- **动机**：解决什么问题，而不是改了什么文件
- **验证方式**：怎么证明它work（测试 / 手动步骤 / 真实音频的对比）
- **对三大原则的影响**：是否让系统更"吵"了？是否引入了主动行为？

---

## 三条不可妥协的原则

任何 PR 只要触碰以下任一条，都会被拒绝，没有讨论余地：

1. **不主动发声、不弹窗、不建议。** 影书是单向输入通道。
2. **不执行非主人声纹授权的指令。**
3. **不为了"看起来聪明"而编造记忆。** 宁可返回空。

---

## 报告问题

用 [issue 模板](.github/ISSUE_TEMPLATE/)。请附上：

- `shadowscribe doctor` 的输出
- `docker compose logs worker` 的相关片段
- 如果是抽取质量问题：**原始转写**与**生成的卡片**

⚠️ **不要提交真实对话内容**（涉及他人隐私），请脱敏或改用构造的样例。

---

## 安全

发现安全漏洞请**不要**开公开 issue。见 [SECURITY.md](SECURITY.md)。

---

## License

贡献即表示同意以 [MIT](LICENSE) 授权。
