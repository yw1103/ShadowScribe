# 记忆模型

影书记忆分两层：**自己的感官账本**（SQLite，`recordings`/`segments`/`episodes`/…）
和**交出去的因果记忆**（`causal-memory` 或 native 后端）。

为什么分两层：感官账本是"我收到了什么、处理到哪一步"，是**可重建**的；
因果记忆是"这世界因此变成了什么样"，是**长期资产**。混在一起会让"重跑管线"
变成一件危险的事。

---

## 1. 抽取出的结构

`pipeline/distill.py` 让 LLM 输出严格 JSON，结构如下：

```json
{
  "title": "登录页排期对齐",
  "summary": "老王反馈 Safari 白屏影响用户投诉，双方同意优先修复兼容性。",
  "topics": ["登录页", "排期"],
  "entities": [
    {"name": "老王", "kind": "person", "aliases": ["王工"]},
    {"name": "登录页", "kind": "project", "aliases": []}
  ],
  "facts": [
    {"key": "客户_老王", "value": "后端负责人，关注交付排期", "confidence": 0.9}
  ],
  "edges": [
    {
      "cause": "老王反馈登录页 Safari 白屏、用户投诉多",
      "effect": "我承诺周四之前提交修复方案",
      "relation": "caused",
      "actor": "owner",
      "task_tag": "登录页",
      "evidence": "周四之前我给你一个方案",
      "confidence": 0.9
    }
  ],
  "commitments": [
    {
      "what": "提交登录页 Safari 白屏修复方案",
      "owner": "我",
      "counterparty": "老王",
      "due_text": "周四",
      "due_date": "2026-01-08",
      "confidence": 0.9
    }
  ],
  "decisions": [
    {"what": "登录页先兼容 Safari，新功能排期后延", "rationale": "用户投诉集中", "actor": "owner"}
  ]
}
```

---

## 2. 字段语义

### 2.1 `edges[]` — 因果边

| 字段 | 说明 |
|---|---|
| `cause` | 触发事件：谁提出了什么问题、外部发生了什么状态变更 |
| `effect` | 结果：达成的决策、做出的承诺、导致的后果 |
| `relation` | `caused` / `enabled` / `prevented` / `no_effect`（见下） |
| `actor` | **`owner` = 主人说的/做的**；`other` = 对方；`unknown` = 无法判断 |
| `task_tag` | 2–6 字任务标签，用于 causal-memory 的 `task_tag` 分组与分支比较 |
| `evidence` | 支撑这条因果的**原话片段**（原样摘录）。用于人工复核与防幻觉 |
| `confidence` | 0.0–1.0。证据充分且表述明确 ≥ 0.85；含糊或转写有噪声 < 0.5 |

**`actor` 是整条链路里最关键的字段。** 它决定了记忆里主语是"我"还是"别人"。
写反了，AI 就会以为你承诺了别人该做的事，或者漏掉你真正的承诺。

### 2.2 四种关系

| 值 | 中文 | 语义 | 在 causal-memory 中的激活系数 |
|---|---|---|---|
| `caused` | 导致 | "因为 X，所以 Y" | +1.0（强兴奋） |
| `enabled` | 促成 | "X 帮助了 Y，但不是唯一原因" | +0.5（弱兴奋） |
| `prevented` | 避免/阻止 | "因为做了 X，所以 Y 没发生" | **−0.3（抑制）** |
| `no_effect` | 无影响 | 明确验证过没有因果关系 | 0.0 |

`prevented` 是最容易被忽略但价值最高的一类：它记录了**避开的坑**。
"上线前跑了回归测试，所以没有出现上次那种事故"——这条记忆会在你下次想跳过测试时
以负激活的形式提醒你。

### 2.3 `commitments[]` — 承诺

| 字段 | 说明 |
|---|---|
| `what` | 具体要交付什么 |
| `owner` | 承诺人。`我` 或对方的名字 |
| `counterparty` | 对谁承诺的 |
| `due_text` | **原文里的时间说法**（`周四` / `下周一` / `月底`） |
| `due_date` | 换算成 `YYYY-MM-DD`，无法确定则 `null` |
| `confidence` | 抽取置信度 |

`due_text` 和 `due_date` 都保留是有意的：`due_text` 是**证据**，
`due_date` 是**可计算的**。当换算出错时，你能看到原文是怎么说的。

换算依赖 `SS_TIMEZONE` 与录音日期——这也是为什么接入协议里
**强烈建议手机端填 `recorded_at`**。

### 2.4 `facts[]` — 扁平事实

稳定的、不随对话变化的客观事实，如人物角色、项目技术栈、客户偏好。
写入 `causal-memory.record_fact(key, value, scope, confidence)`。

key 建议用 `类别_对象` 形式（`客户_老王`、`项目_登录页`），便于按前缀检索。

### 2.5 `entities[]` — 实体索引

轻量索引，让 `ss search 老王` 足够快、足够可预期。
`kind` 取值：`person` / `org` / `artifact` / `project` / `place` / `other`。

---

## 3. 写入映射

`memory/causal_memory_backend.py` 把上面结构映射到 `causal-memory`：

| 影书 | causal-memory | 说明 |
|---|---|---|
| `edges[i]` | `record_decision(decision, outcome, relation, task_tag, confidence_source="llm_inferred", context=...)` | `decision` 带着 `【我】`/`【对方】` 前缀 |
| `facts[i]` | `record_fact(key, value, scope, confidence, replace_same_key)` | |
| 无直接对应 | `remember(messages)` | **默认不用**——影书已经做了抽取，重复抽取会翻倍 LLM 成本 |

`context` 参数被拼成：

```
{session_hint} | {episode.title} | 说话人={actor} | 原话：{evidence}
```

原因：causal-memory 用 `(task_tag, context)` 作为**可比分支（fork）**的键。
把说话人和会议标题编码进去，"同一项目、同一次会议"的分支才真的可比，
反事实查询（`counterfactual_query`）才有意义。

---

## 4. 检索

三种检索路径并存，各有明确适用场景：

| 入口 | 实现 | 适合 |
|---|---|---|
| `GET /v1/context/brief` | 影书直接读 `episodes`/`commitments` 表 | "给我今天的全貌" |
| `GET /v1/search` | causal-memory 语义/扩散检索 **+** `segments` 原文子串扫描 | "当初到底怎么说的" |
| MCP `search_reality` | 同上 | 编辑器内按需检索 |

`/v1/search` 同时返回**结构化命中**和**原始转写片段**，这是刻意的：
结构化记忆可能因为抽取遗漏而不命中，但原文永远在那里。两者互补，
用户不会遇到"我记得说过，但搜不到"的情况。

---

## 5. 为什么 `episodes.raw_json` 保留完整抽取结果

```sql
SELECT raw_json FROM episodes WHERE id = ?;
-- {"edges": [...], "decisions": [...], "degraded": false}
```

这是**有意的冗余**。三个理由：

1. 上下文卡片可以完全从影书自己的表重建，不依赖记忆后端的可用性。
2. 换记忆后端（`causal-memory` ↔ `native`）时历史不丢。
3. 蒸馏提示词改进后，可以用旧转写重新抽取而不必重新跑 ASR
   （ASR 是最慢的一步）。

---

## 6. 降级行为

| 缺失条件 | 后果 |
|---|---|
| 没有 `SS_LLM_API_KEY` | `episodes` 仍会创建，`summary` 是转写原文，`edges` 为空，`raw_json.degraded = true`。时间轴和原文搜索仍可用，但没有因果记忆 |
| 没有录入声纹 / `SS_DIARIZATION=off` | 所有 `segments.speaker` 为 `unknown`，蒸馏器从措辞推断 `actor` |
| `causal-memory` 未安装 | 自动降级到 `native` 后端（SQLite + 中文 bigram 匹配） |
| 单个因果边写入失败 | 记录错误日志，**其余边继续写入**。一条坏边不会毁掉整个片段 |
