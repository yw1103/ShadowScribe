"""Distillation — turning "what was said" into "what it means".

This is where ShadowScribe earns its keep. A transcript is noise; a
``cause → decision/commitment`` edge is something a desktop agent can act on
without being briefed.

The extractor is intentionally strict. It is far better to return an empty episode
than to invent a commitment the user never made — a hallucinated promise poisons
every downstream context injection.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from ..memory import CausalEdge, EpisodeInput, Fact
from .asr import AsrSegment
from .diarize import GUEST, OWNER, UNKNOWN

log = logging.getLogger(__name__)

# --------------------------------------------------------------------- prompt

SYSTEM_PROMPT = """\
你是一个「因果记录员」。你的唯一任务：从一段真实的多方对话转写中，抽取结构化事实与因果关系。

铁律（违反任何一条都算失败）：
1. 只记录转写文本中**真实出现过**的信息。严禁推测、补全、润色或加入常识。
2. 转写有识别错误时按语义合理纠正用词，但不得改变事实本身。
3. 因果关系必须有明确语言依据。没有因果证据就不要生成边。
4. `actor` 判断是全表最重要的字段。写反会让「对方欠我的交付」变成「我欠对方的交付」，
   后果严重。按以下优先级判断：
   a) 该句说话人标注为「主人」→ owner；标注为「对方」→ other。
   b) 若标注**全是「未知」**（本次录音没有声纹信息），用「谁欠谁一个交付」判别：
      · **owner（主人）= 录音的持有者**，是会拍板、会承诺交付的那一方。
      · 会话背景提示里出现的人名（例如「与老王、张总开会」里的老王、张总）→ other。
      · 第一人称做出承诺、安排交付、拍板决定
        （"那我周三之前先定位""我给你一个方案""那就这么定""我周五之前发群里"）→ owner。
      · 第一人称反馈问题、提需求、汇报进展
        （"我看了下用户反馈""我这边收到投诉""我怀疑是…不兼容"）→ other，
        因为这是对方在**向主人提需求**，而不是在承诺交付。
      · 仍然无法判断 → unknown。**宁可 unknown 也不要猜。**
5. 「承诺」必须是明确的待办或交付：有具体动作，且最好有时间或交付物。
   "以后再说"、"看看情况" 不算承诺。
6. 没有可抽取内容时，数组一律返回空，不要为了凑数而编造。
7. 所有输出使用与转写相同的语言（中文转写就用中文），不要翻译。
   输出一律使用**简体中文**，即使转写里出现了繁体字。
8. 只输出 JSON，不要输出任何解释文字或 markdown 代码块。"""

USER_TEMPLATE = """\
今天的日期是 {today}。

会话背景提示：{session_hint}
录音时间：{recorded_at}

转写文本（格式 [序号] 说话人: 内容）：
---
{transcript}
---

注意：如果所有行的说话人都是「未知」，说明本次录音没有声纹标注。
此时请严格按铁律 4b，用「谁欠谁一个交付」判断 actor。

请抽取并严格按以下 JSON 结构输出：
{{
  "title": "用不超过 20 字概括这段对话在谈什么",
  "summary": "2-4 句话客观概括，不含评价",
  "topics": ["2-6字名词短语", "..."],
  "entities": [
    {{"name": "人名/项目名/产品名", "kind": "person|org|artifact|project|place|other",
      "aliases": ["可能的别名"]}}
  ],
  "facts": [
    {{"key": "简短的键，如 客户_老王", "value": "稳定的客观事实陈述", "confidence": 0.0}}
  ],
  "edges": [
    {{"cause": "触发事件（谁提出了什么/发生了什么变更）",
      "effect": "导致的结果、达成的决策、或做出的承诺",
      "relation": "caused|enabled|prevented|no_effect",
      "actor": "owner|other|unknown",
      "task_tag": "2-6字任务标签，如 登录页",
      "evidence": "支撑这条因果的原文片段（原样摘录）",
      "confidence": 0.0}}
  ],
  "commitments": [
    {{"what": "具体要交付什么",
      "owner": "承诺人（我 / 对方的名字）",
      "counterparty": "对谁承诺的",
      "due_text": "原文中的时间说法，如 周四 / 下周一 / 月底，没有则 null",
      "due_date": "换算成 YYYY-MM-DD，无法确定则 null",
      "evidence": "做出这个承诺的原文片段（原样摘录）",
      "confidence": 0.0}}
  ],
  "decisions": [
    {{"what": "最终拍板的决定", "rationale": "决定依据", "actor": "owner|other|unknown"}}
  ]
}}

confidence 取 0.0-1.0。证据充分、表述明确给 0.85 以上；语气含糊、转写有噪声给 0.5 以下。"""


class DistillError(RuntimeError):
    pass


@dataclass(slots=True)
class CommitmentOut:
    what: str
    owner: str = "我"
    counterparty: str | None = None
    due_text: str | None = None
    due_date: str | None = None
    confidence: float = 0.0
    evidence: str | None = None


@dataclass(slots=True)
class DecisionOut:
    what: str
    rationale: str | None = None
    actor: str = UNKNOWN


@dataclass(slots=True)
class DistillationResult:
    title: str = ""
    summary: str = ""
    topics: list[str] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    edges: list[CausalEdge] = field(default_factory=list)
    commitments: list[CommitmentOut] = field(default_factory=list)
    decisions: list[DecisionOut] = field(default_factory=list)
    degraded: bool = False
    """True when no LLM was used and this is transcript-only filler."""

    def to_episode_input(self, *, occurred_at: str, session_context: str | None) -> EpisodeInput:
        return EpisodeInput(
            title=self.title,
            summary=self.summary,
            edges=self.edges,
            facts=self.facts,
            topics=self.topics,
            occurred_at=occurred_at,
            session_context=session_context,
        )


# ------------------------------------------------------------------ utilities

_SPEAKER_DISPLAY = {OWNER: "主人", GUEST: "对方", UNKNOWN: "未知"}


def render_transcript(segments: list[AsrSegment], labels: list[str] | None = None) -> str:
    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        who = _SPEAKER_DISPLAY.get(labels[i - 1], "未知") if labels else "未知"
        stamp = f"{seg.start_ms // 60000:02d}:{(seg.start_ms // 1000) % 60:02d}"
        lines.append(f"[{i}] {who}({stamp}): {seg.text}")
    return "\n".join(lines)


def chunk_segments(
    segments: list[AsrSegment], window_s: int, *, min_chars: int = 0
) -> list[list[AsrSegment]]:
    """Split by accumulated duration so one LLM call never sees a whole afternoon."""
    if not segments:
        return []
    windows: list[list[AsrSegment]] = []
    current: list[AsrSegment] = []
    start_ms = segments[0].start_ms

    for seg in segments:
        current.append(seg)
        if seg.end_ms - start_ms >= window_s * 1000:
            if sum(len(s.text) for s in current) >= min_chars:
                windows.append(current)
            current = []
            start_ms = seg.end_ms
    if current and sum(len(s.text) for s in current) >= min_chars:
        windows.append(current)
    return windows


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _iso_date(value: Any) -> str | None:
    text = _as_str(value)
    if not text or text.lower() in {"null", "none", "n/a", "unknown"}:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    return m.group(0) if m else None


# -------------------------------------------------------------------- engine


class Distiller:
    """LLM-backed causal extractor with a graceful no-LLM degradation path."""

    def __init__(self, settings) -> None:
        self.s = settings
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------------ http
    @property
    def enabled(self) -> bool:
        return bool(self.s.llm_enabled and self.s.llm_api_key)

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.s.llm_base_url.rstrip("/"),
                timeout=httpx.Timeout(self.s.llm_timeout_s, connect=20.0),
                headers={
                    "Authorization": f"Bearer {self.s.llm_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _chat_json(self, system: str, user: str) -> dict:
        client = self._ensure_client()
        payload = {
            "model": self.s.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.s.llm_temperature,
            "response_format": {"type": "json_object"},
            "max_tokens": 4096,
        }
        last_error: Exception | None = None
        for attempt in range(1, self.s.llm_max_retries + 1):
            try:
                resp = client.post("/chat/completions", json=payload)
                if resp.status_code >= 400:
                    raise DistillError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
                body = resp.json()
                content = body["choices"][0]["message"]["content"]
                return json.loads(_strip_code_fence(content))
            except Exception as exc:
                last_error = exc
                log.warning(
                    "distill attempt %d/%d failed: %s", attempt, self.s.llm_max_retries, exc
                )
        raise DistillError(
            f"LLM extraction failed after {self.s.llm_max_retries} attempts: {last_error}"
        )

    # --------------------------------------------------------------- extract
    def distill(
        self,
        segments: list[AsrSegment],
        *,
        labels: list[str] | None = None,
        recorded_at: str | None = None,
        session_hint: str | None = None,
    ) -> DistillationResult:
        transcript = render_transcript(segments, labels)
        if not self.enabled:
            return self._degraded(segments, labels, recorded_at)

        recorded = recorded_at or date.today().isoformat()
        user = USER_TEMPLATE.format(
            today=recorded,
            session_hint=session_hint or "（无）",
            recorded_at=recorded,
            transcript=transcript,
        )
        data = self._chat_json(SYSTEM_PROMPT, user)
        return self._parse(data, recorded_at=recorded)

    def _degraded(
        self, segments: list[AsrSegment], labels: list[str] | None, recorded_at: str | None
    ) -> DistillationResult:
        """No LLM key: keep the timeline searchable without inventing structure."""
        head = " / ".join(s.text for s in segments[:3])[:60]
        return DistillationResult(
            title=head or "(未命名片段)",
            summary=render_transcript(segments, labels)[:800],
            degraded=True,
        )

    # ----------------------------------------------------------------- parse
    def _parse(self, data: dict, *, recorded_at: str) -> DistillationResult:
        if not isinstance(data, dict):
            raise DistillError(f"expected a JSON object, got {type(data).__name__}")

        result = DistillationResult(
            title=_as_str(data.get("title"))[:120],
            summary=_as_str(data.get("summary")),
            topics=[_as_str(t) for t in _as_list(data.get("topics")) if _as_str(t)][:12],
        )

        for ent in _as_list(data.get("entities")):
            if not isinstance(ent, dict):
                continue
            name = _as_str(ent.get("name"))
            if not name:
                continue
            result.entities.append(
                {
                    "name": name,
                    "kind": _as_str(ent.get("kind")) or "other",
                    "aliases": [_as_str(a) for a in _as_list(ent.get("aliases")) if _as_str(a)],
                }
            )

        for fact in _as_list(data.get("facts")):
            if not isinstance(fact, dict):
                continue
            key, value = _as_str(fact.get("key")), _as_str(fact.get("value"))
            if key and value:
                result.facts.append(
                    Fact(key=key, value=value, confidence=_as_float(fact.get("confidence"), 0.6))
                )

        task_fallback = result.topics[0] if result.topics else "general"
        for edge in _as_list(data.get("edges")):
            if not isinstance(edge, dict):
                continue
            cause, effect = _as_str(edge.get("cause")), _as_str(edge.get("effect"))
            if not cause or not effect:
                continue
            actor = _as_str(edge.get("actor")).lower()
            if actor not in {OWNER, GUEST, "other", UNKNOWN}:
                actor = UNKNOWN
            if actor == GUEST:
                actor = "other"
            result.edges.append(
                CausalEdge(
                    cause=cause,
                    effect=effect,
                    relation=_as_str(edge.get("relation")) or "caused",
                    task_tag=_as_str(edge.get("task_tag")) or task_fallback,
                    actor=actor,
                    evidence=_as_str(edge.get("evidence")) or None,
                    confidence=_as_float(edge.get("confidence"), 0.6),
                    occurred_at=recorded_at,
                )
            )

        for item in _as_list(data.get("commitments")):
            if not isinstance(item, dict):
                continue
            what = _as_str(item.get("what"))
            if not what:
                continue
            result.commitments.append(
                CommitmentOut(
                    what=what,
                    owner=_as_str(item.get("owner")) or "我",
                    counterparty=_as_str(item.get("counterparty")) or None,
                    due_text=_as_str(item.get("due_text")) or None,
                    due_date=_iso_date(item.get("due_date")),
                    confidence=_as_float(item.get("confidence"), 0.6),
                    evidence=_as_str(item.get("evidence")) or None,
                )
            )

        for item in _as_list(data.get("decisions")):
            if not isinstance(item, dict):
                continue
            what = _as_str(item.get("what"))
            if not what:
                continue
            actor = _as_str(item.get("actor")).lower()
            result.decisions.append(
                DecisionOut(
                    what=what,
                    rationale=_as_str(item.get("rationale")) or None,
                    actor=OWNER if actor == OWNER else ("other" if actor == GUEST else UNKNOWN),
                )
            )

        return result

    def distill_long(
        self,
        segments: list[AsrSegment],
        *,
        labels: list[str] | None = None,
        recorded_at: str | None = None,
        session_hint: str | None = None,
    ) -> list[DistillationResult]:
        """Distil every window of a long recording."""
        windows = chunk_segments(
            segments, self.s.distill_window_s, min_chars=self.s.distill_min_chars
        )
        if not windows:
            return []
        results: list[DistillationResult] = []
        offset = 0
        for i, window in enumerate(windows, start=1):
            sub_labels = labels[offset : offset + len(window)] if labels else None
            log.info("distilling window %d/%d (%d segments)", i, len(windows), len(window))
            try:
                results.append(
                    self.distill(
                        window,
                        labels=sub_labels,
                        recorded_at=recorded_at,
                        session_hint=session_hint,
                    )
                )
            except DistillError as exc:
                log.error("window %d distillation failed: %s", i, exc)
                results.append(self._degraded(window, sub_labels, recorded_at))
            offset += len(window)
        return results
