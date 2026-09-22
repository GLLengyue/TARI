"""Rewrite mode: one instruction in, a same-scale long-form retelling out.

The reader says what they want to be different, once. From there the system
writes the whole book, chapter by chapter, on top of the compilation products
(chapter cards, story arcs, world knowledge) that already exist.

What makes long-form generation fail is drift, not prose quality: the model
forgets what it established, slides back onto the original track, or drops a
thread it planted. Runtime is the natural place to fight that, because runtime
is the thing that remembers. Three mechanisms do the work here:

* the rewrite premise sits in a static prefix and is repeated every chapter;
* every divergence from the source is journaled as a ledger entry, and the
  ledger is never compacted -- only prose summaries are;
* each chapter is checked against the ledger after it is written.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..llm import LLMSettings, OpenAICompatibleClient, resolve_llm_settings
from .decomposer import ChapterCard, StoryCompilationWorkspace

REWRITE_VERSION = 1

#: Default target length of one chapter, in characters.
DEFAULT_WORDS_PER_CHAPTER = 2000

#: How many per-chapter summaries stay verbatim before being rolled up.
DEFAULT_RECENT_SUMMARIES = 4

#: How many relationship edges from the source baseline reach the prompt.
RELATIONSHIP_BASELINE = 120

#: Characters of the world profile the brief is allowed to read.
BRIEF_PROFILE_BUDGET = 4500

#: Characters of real source prose the brief reads to judge tone.
BRIEF_SAMPLE_BUDGET = 2500

#: Attempts allowed per chapter before it is recorded as a failure.
MAX_CHAPTER_ATTEMPTS = 3

#: Rough characters-per-token ratio, used only for the compaction decision.
_CHARS_PER_TOKEN = 1.6

#: Compact when the assembled prompt exceeds this many estimated tokens.
CONTEXT_COMPACT_TOKENS = 16000

CHAPTER_CONTRACT = (
    "# 长篇仿写契约\n"
    "你在写一部基于已有作品的二次创作长篇。原作已经拆解完毕，你按章推进，一次一章。\n"
    "\n"
    "## 不可动摇\n"
    "- **改写前提贯穿全书。** 它是这本书存在的理由；任何一章都不得滑回原作轨道。\n"
    "- **已写下的既定事实不可推翻。** 后文与前文冲突时，改后文，不改前文。"
    "下方的事实台账就是这条的记录，逐条遵守。\n"
    "- **伏笔要收。** 已经埋下的线索，在后文兑现；不要只埋不收。\n"
    "\n"
    "## 每一章怎么做\n"
    "- 只写本章。不要概括全书，不要预告下一章，不要在结尾做总结。\n"
    "- **改写计划优先于原作梗概。** 原作大纲是参照，不是义务：计划要求不同走向时按计划写，"
    "计划未涉及的部分才按原作推进，且本章内的因果要自洽。\n"
    "- 不要为了保住原作情节而把改写推给后文 —— 计划里写着本章要发生的事，本章就发生。\n"
    "- 人物要像他自己。改写改变的是处境、关系与选择，不是所有人的性格底色。\n"
    "\n"
    "## 笔法\n"
    "- 用具体场景写，不用「他度过了艰难的一天」这类总结句。\n"
    "- 对话要有辨识度：谁在说话应当能认出来。\n"
    "- 场景之间保持时间与空间的连续；不要无交代地跳场。\n"
    "- 不使用现代用语，不跳出故事，不解释你在做什么。\n"
    "\n"
    "## 输出\n"
    "只输出本章正文。没有标题，没有小标题，没有解释。\n"
)

BRIEF_CONTRACT = (
    "你在帮读者把一句模糊的愿望，变成一份可执行的二创方案。\n"
    "读者会说「我想看到什么不同」。你要把它拆成：\n"
    "- premise：一句话说清这本书的前提变化；\n"
    "- divergences：必须改变的点（具体到人物、关系或事件）；\n"
    "- invariants：必须保留的点（人物的性格底色、世界的规则）；\n"
    "- tone：语体要求，要能指认原作的语言特征（句式、节奏、称呼习惯），"
    "若读者没提就沿用原作的语体 —— 依据是给出的原作正文样本，不要凭情节大纲猜；\n"
    "- plan：**分章改写计划**，这是最要紧的一项。读者给的原作章节清单会告诉你"
    "原作每一章讲什么、某个人物原本到第几章才出场。据此写出 3-8 条计划，每条：\n"
    "  chapter（从第几章开始生效）+ change（这一段的改写要求，具体到谁做什么）。\n"
    "  写法要求：\n"
    "  · 改写前提若涉及「某个人物早点出现/早点改变」，就必须有一条计划明确"
    "他在第几章登场、那场戏是什么样的，不能等原作让他出场；\n"
    "  · 计划要落在具体章号上，不要写成「全书保持」这种空话；\n"
    "  · 第一条计划通常从第 1 章开始，因为读者要的就是「一开始就不同」；\n"
    "  · 不要改写读者的原意，只把它翻译成可执行的章节安排。\n"
    "只返回 JSON："
    '{"premise":"...","divergences":["..."],"invariants":["..."],"tone":"...",'
    '"plan":[{"chapter":1,"change":"..."}]}'
)

EXTRACT_CONTRACT = (
    "给你一章新写的正文，以及它所对应的原作情节。提炼索引信息，供后续章节使用。\n"
    "- summary：这一章发生了什么，300 字以内，只写事实与因果，不写评价；\n"
    "- entries：**与原作不同的**、且此后必须成立的既定事实。"
    "判断标准是「原作里没有这样发展」：原作本来就有的人物、事件与关系不算偏离，"
    "不要写进 entries；只有改写带来的变化才算 —— 某人的动机或立场变了、某件事的结果不同了、"
    "某段关系被改写、原作此时尚未出现的人提前出现了。"
    "只记录会影响后续连续性的（人物状态、关系、承诺、物品归属、关键事件），每条一句话。\n"
    "若本章完全按原作推进、没有任何偏离，entries 返回空列表。"
    "空列表是正常结果，不要把「本章发生了什么」当成偏离。\n"
    "只返回 JSON："
    '{"summary":"...","entries":[{"statement":"...","kind":"divergence"}]}\n'
    "kind 取值：divergence / consequence / character / relationship / object / promise"
)

VERIFY_CONTRACT = (
    "你是长篇一致性审查者。给你本章的摘要与新增事实，以及此前已确立的事实台账。\n"
    "检查：本章是否推翻或无视了台账中的任何一条？是否重复叙述了已经发生过的事件？\n"
    "不要因为文风、节奏或细节取舍而拒绝。只报告**硬冲突**。\n"
    '只返回 JSON：{"accepted":true,"violations":[]}；'
    "有冲突时 accepted=false 并在 violations 里逐条写明是违背了哪一条。"
)

COMPACT_CONTRACT = (
    "下面是若干连续的章节摘要。请把它们压缩成一段连贯的情节梗概，"
    "保留人物状态的变化、承诺与代价、以及因果链；删去重复与修饰。\n"
    "只输出梗概本身，不要标题。"
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _log(message: str) -> None:
    print(f"[{_now()}] {message}", flush=True)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------
# Domain model
# --------------------------------------------------------------------------


class RewritePlanPoint(BaseModel):
    """Where the retelling diverges from the source, and how.

    A premise alone cannot steer a long book. Without per-chapter
    instructions the writer's only concrete input is the source's own outline,
    and the "rewrite" degrades into a paraphrase of it -- which is what the
    first live run produced, chapter after chapter.
    """

    chapter: int
    change: str


class RewriteBrief(BaseModel):
    """The reader's single sentence, expanded into workable constraints."""

    instruction: str
    premise: str = ""
    divergences: list[str] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    tone: str = ""
    plan: list[RewritePlanPoint] = Field(default_factory=list)


class LedgerEntry(BaseModel):
    """One fact that differs from the source and must hold from now on."""

    entry_id: str
    chapter: int
    statement: str
    kind: Literal["divergence", "consequence", "character", "relationship", "object", "promise"] = (
        "divergence"
    )


class ChapterSummary(BaseModel):
    chapter: int
    title: str
    summary: str
    entries: list[str] = Field(default_factory=list)


class ChapterDraft(BaseModel):
    chapter: int
    title: str
    prose: str
    summary: str
    entries: list[LedgerEntry] = Field(default_factory=list)


class RewriteState(BaseModel):
    """Everything needed to resume: the brief, the ledger, and what is done."""

    schema_version: int = REWRITE_VERSION
    source_id: str
    brief: RewriteBrief
    total_chapters: int = 0
    target_chapters: int = 0
    words_per_chapter: int = DEFAULT_WORDS_PER_CHAPTER
    ledger: list[LedgerEntry] = Field(default_factory=list)
    summaries: list[ChapterSummary] = Field(default_factory=list)
    rolling_summary: str = ""
    completed: list[int] = Field(default_factory=list)
    failures: dict[str, str] = Field(default_factory=dict)
    started_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class RewriteConflict(ValueError):
    """The chapter contradicts the ledger; the draft is not published."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("consistency review rejected this chapter: " + "; ".join(violations))


# --------------------------------------------------------------------------
# Workspace
# --------------------------------------------------------------------------


class RewriteWorkspace:
    """Filesystem boundary for a resumable retelling."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def state_path(self) -> Path:
        return self.root / "rewrite.json"

    @property
    def chapters_dir(self) -> Path:
        return self.root / "chapters"

    def chapter_path(self, chapter: int) -> Path:
        return self.chapters_dir / f"chapter_{chapter:04d}.md"

    def meta_path(self, chapter: int) -> Path:
        return self.chapters_dir / f"chapter_{chapter:04d}.meta.json"

    def ensure(self) -> None:
        self.chapters_dir.mkdir(parents=True, exist_ok=True)

    def read_json(self, path: Path, default: Any = None) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def write_json(self, path: Path, payload: Any) -> None:
        _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def write_text(self, path: Path, content: str) -> None:
        _atomic_write(path, content.rstrip() + "\n")

    def load_state(self) -> RewriteState | None:
        payload = self.read_json(self.state_path, None)
        if not isinstance(payload, dict):
            return None
        return RewriteState.model_validate(payload)

    def save_state(self, state: RewriteState) -> None:
        state.updated_at = _now()
        self.write_json(self.state_path, state.model_dump(mode="json"))

    def chapter_text(self, chapter: int) -> str:
        try:
            return self.chapter_path(chapter).read_text(encoding="utf-8").strip()
        except OSError:
            return ""


# --------------------------------------------------------------------------
# Authors
# --------------------------------------------------------------------------


class RewriteAuthor(ABC):
    """The model-facing surface of rewrite mode."""

    @abstractmethod
    async def plan_brief(
        self, instruction: str, world_profile: str, sample: str, chapter_titles: str = ""
    ) -> RewriteBrief:
        """Expand one reader sentence into a premise and a per-chapter plan.

        ``chapter_titles`` is the source's chapter list: it is how the planner
        learns that a character the reader wants earlier only appears at
        chapter nine, and can therefore schedule the divergence.
        """
        raise NotImplementedError

    @abstractmethod
    async def write_chapter(self, messages: Sequence[dict[str, str]]) -> str:
        raise NotImplementedError

    @abstractmethod
    async def extract_chapter(
        self, chapter: int, title: str, prose: str, source_outline: str = ""
    ) -> tuple[str, list[dict[str, Any]]]:
        """Return (summary, raw entries) for one written chapter.

        ``source_outline`` is how the original covered this chapter. Without it
        the extractor cannot tell a divergence from something that happens in
        the source anyway, and the ledger fills up with retold plot.
        """
        raise NotImplementedError

    @abstractmethod
    async def verify_chapter(
        self, summary: str, statements: Sequence[str], ledger: Sequence[str]
    ) -> tuple[bool, list[str]]:
        raise NotImplementedError

    @abstractmethod
    async def compact_summaries(self, summaries: Sequence[str]) -> str:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class OpenAIRewriteAuthor(RewriteAuthor):
    """Rewrite author over the shared OpenAI-compatible transport."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        *,
        client: Any | None = None,
        transport: Any | None = None,
    ) -> None:
        self.settings = settings or resolve_llm_settings()
        self._client = OpenAICompatibleClient(self.settings, client=client, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def plan_brief(
        self, instruction: str, world_profile: str, sample: str, chapter_titles: str = ""
    ) -> RewriteBrief:
        payload = await self._client.complete_json(
            [
                {"role": "system", "content": BRIEF_CONTRACT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "读者的要求": instruction,
                            "原作世界档案": world_profile[:6000],
                            "原作正文样本": sample[:3000],
                            "原作章节清单": chapter_titles[:4000],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=2000,
        )
        plan: list[RewritePlanPoint] = []
        for item in payload.get("plan") or []:
            if not isinstance(item, dict):
                continue
            change = str(item.get("change") or "").strip()
            if not change:
                continue
            try:
                chapter = int(item.get("chapter") or 1)
            except (TypeError, ValueError):
                chapter = 1
            plan.append(RewritePlanPoint(chapter=max(1, chapter), change=change))
        return RewriteBrief(
            instruction=instruction,
            premise=str(payload.get("premise") or instruction),
            divergences=[str(item) for item in payload.get("divergences") or []],
            invariants=[str(item) for item in payload.get("invariants") or []],
            tone=str(payload.get("tone") or ""),
            plan=sorted(plan, key=lambda point: point.chapter),
        )

    async def write_chapter(self, messages: Sequence[dict[str, str]]) -> str:
        text = await self._client.complete_text(list(messages))
        return text.strip()

    async def extract_chapter(
        self, chapter: int, title: str, prose: str, source_outline: str = ""
    ) -> tuple[str, list[dict[str, Any]]]:
        payload = await self._client.complete_json(
            [
                {"role": "system", "content": EXTRACT_CONTRACT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "chapter": chapter,
                            "title": title,
                            "原作本章情节": source_outline,
                            "本章正文": prose,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=1200,
        )
        summary = str(payload.get("summary") or "").strip()
        entries = [item for item in payload.get("entries") or [] if isinstance(item, dict)]
        return summary, entries

    async def verify_chapter(
        self, summary: str, statements: Sequence[str], ledger: Sequence[str]
    ) -> tuple[bool, list[str]]:
        payload = await self._client.complete_json(
            [
                {"role": "system", "content": VERIFY_CONTRACT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "本章摘要": summary,
                            "本章新增事实": list(statements),
                            "既有事实台账": list(ledger),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=800,
        )
        accepted = bool(payload.get("accepted", True))
        violations = [str(item) for item in payload.get("violations") or []]
        return accepted and not violations, violations

    async def compact_summaries(self, summaries: Sequence[str]) -> str:
        return (
            await self._client.complete_text(
                [
                    {"role": "system", "content": COMPACT_CONTRACT},
                    {"role": "user", "content": "\n\n".join(summaries)},
                ],
                temperature=0.2,
            )
        ).strip()


class FakeRewriteAuthor(RewriteAuthor):
    """Deterministic author for offline tests and dry runs."""

    #: Set to a substring to make the matching chapter fail verification once.
    conflict_on: str | None = None

    def __init__(self, *, conflict_on: str | None = None, compact_marker: bool = True) -> None:
        self.conflict_on = conflict_on
        self.compact_marker = compact_marker
        self.write_calls = 0
        self.verify_calls = 0
        #: The source outline handed to each extract call, in chapter order.
        self.extract_outlines: list[str] = []
        #: What each plan_brief call actually received.
        self.brief_inputs: list[dict[str, str]] = []

    async def plan_brief(
        self, instruction: str, world_profile: str, sample: str, chapter_titles: str = ""
    ) -> RewriteBrief:
        self.brief_inputs.append(
            {"profile": world_profile, "sample": sample, "titles": chapter_titles}
        )
        return RewriteBrief(
            instruction=instruction,
            premise=instruction,
            divergences=[f"按读者的要求改变：{instruction}"],
            invariants=["人物性格底色不变"],
            tone="沿用原作语体",
            plan=[RewritePlanPoint(chapter=1, change=f"从第 1 章起：{instruction}")],
        )

    async def write_chapter(self, messages: Sequence[dict[str, str]]) -> str:
        self.write_calls += 1
        marker = instruction_marker(messages)
        return (
            f"（第 N 章正文·{marker}）雨落在瓦上，声音很密。"
            "人物按本章大纲推进，事件依次发生；主角作出与改写前提一致的选择，"
            "而未改写的世界照旧运转。"
        )

    async def extract_chapter(
        self, chapter: int, title: str, prose: str, source_outline: str = ""
    ) -> tuple[str, list[dict[str, Any]]]:
        self.extract_outlines.append(source_outline)
        return (
            f"第 {chapter} 章：{title}。按大纲推进，主角作出与改写前提一致的选择。",
            [
                {
                    "statement": f"第 {chapter} 章确立了偏离：{title}",
                    "kind": "divergence",
                }
            ],
        )

    async def verify_chapter(
        self, summary: str, statements: Sequence[str], ledger: Sequence[str]
    ) -> tuple[bool, list[str]]:
        self.verify_calls += 1
        if self.conflict_on and any(self.conflict_on in item for item in statements):
            # Reject exactly once per offending chapter so retries can succeed.
            self.conflict_on = None
            return False, ["与台账冲突：本章新增事实与既有记录矛盾"]
        return True, []

    async def compact_summaries(self, summaries: Sequence[str]) -> str:
        joined = " ".join(item.strip() for item in summaries)
        prefix = "（压缩梗概）" if self.compact_marker else ""
        return prefix + joined[:600]


def instruction_marker(messages: Sequence[dict[str, str]]) -> str:
    """Read the rewrite premise back out of an assembled prompt (for tests)."""
    for message in messages:
        content = message.get("content") or ""
        marker = "改写前提："
        if marker in content:
            tail = content.split(marker, 1)[1]
            return tail.splitlines()[0].strip() or "未标注"
    return "未标注"


# --------------------------------------------------------------------------
# Context assembly
# --------------------------------------------------------------------------


def _brief_block(brief: RewriteBrief) -> str:
    lines = ["# 改写前提（全书贯穿）", f"读者要求：{brief.instruction}"]
    if brief.premise:
        lines.append(f"改写前提：{brief.premise}")
    if brief.divergences:
        lines.append("必须改变：" + "；".join(brief.divergences))
    if brief.invariants:
        lines.append("必须保留：" + "；".join(brief.invariants))
    if brief.tone:
        lines.append(f"语体：{brief.tone}")
    return "\n".join(lines)


def _ledger_block(ledger: Sequence[LedgerEntry]) -> str:
    if not ledger:
        return "# 事实台账\n（尚无记录。本章确立的偏离会写入这里，此后不得推翻。）"
    lines = ["# 事实台账（已确立，不得推翻）"]
    lines.extend(f"- 第 {entry.chapter} 章｜{entry.kind}｜{entry.statement}" for entry in ledger)
    return "\n".join(lines)


def _relationship_lines(relationships: Any, *, limit: int = RELATIONSHIP_BASELINE) -> list[str]:
    """Render the source's relationship baseline as prompt lines."""
    if not isinstance(relationships, list) or not relationships:
        return []
    lines = ["# 人物关系（原作基线；本次改写可能改变其中若干条）"]
    for item in relationships[:limit]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        description = str(item.get("description") or "").strip()
        if not label and not description:
            continue
        lines.append(
            f"- {label}：{description}" if label and description else f"- {label or description}"
        )
    return lines if len(lines) > 1 else []


def _plan_block(brief: RewriteBrief, chapter: int) -> str:
    """The rewrite instructions in force for one chapter.

    Every point up to this chapter stays in force, not just the most recent
    one: a divergence scheduled from chapter 1 that the writer has not honoured
    yet must not quietly expire because a later point exists.
    """
    active = [point for point in brief.plan if point.chapter <= chapter]
    if not active:
        return ""
    lines = ["# 改写计划（本章必须兑现）"]
    lines.extend(f"- 第 {point.chapter} 章起：{point.change}" for point in active)
    return "\n".join(lines) + "\n"


def _source_outline(card: ChapterCard) -> str:
    """How the original covered one chapter.

    The extractor needs this to tell a divergence from retold plot; without it
    every chapter of the source reads as a divergence.
    """
    parts = [card.chapter_outline_600.strip()]
    if card.story_line.strip():
        parts.append("情节线：" + card.story_line.strip())
    return "\n".join(part for part in parts if part)


def _history_block(state: RewriteState) -> str:
    """Every summary still held verbatim, plus whatever was compacted earlier.

    Deliberately not windowed to the last few: ``compact`` is what trims this
    list, and anything it has not folded into the rolling summary must stay
    visible. Windowing here as well would silently drop the middle of the book.
    """
    chunks: list[str] = []
    if state.rolling_summary.strip():
        chunks.append("# 早年情节梗概（压缩）\n" + state.rolling_summary.strip())
    if state.summaries:
        lines = ["# 逐章摘要"]
        lines.extend(
            f"- 第 {item.chapter} 章《{item.title}》：{item.summary}" for item in state.summaries
        )
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def build_chapter_messages(
    state: RewriteState,
    card: ChapterCard,
    world_profile: str,
    *,
    attempt_note: str = "",
) -> list[dict[str, str]]:
    """Assemble the prompt for one chapter: prefix, history, ledger, task.

    The ledger sits after the history and stays complete: the model must never
    lose the constraints, while old prose may be summarised away.
    """
    system = "\n\n".join(
        part
        for part in (
            CHAPTER_CONTRACT,
            world_profile.strip(),
            _brief_block(state.brief),
        )
        if part.strip()
    )
    user_parts = [
        _history_block(state),
        _ledger_block(state.ledger),
        "# 本章任务\n"
        f"写作第 {card.chapter} 章《{card.title}》。\n"
        f"目标长度：{state.words_per_chapter} 字左右。\n"
        + _plan_block(state.brief, card.chapter)
        + f"原作本章梗概（仅供参照）：{card.chapter_outline_600.strip()}\n"
        + f"原作情节线（可调整）：{card.story_line.strip() or '（未提供）'}\n"
        + ("本章要点：" + "；".join(card.highlights) + "\n" if card.highlights else "")
        + "只写这一章。",
    ]
    if attempt_note:
        user_parts.append(attempt_note)
    messages = [{"role": "system", "content": system}]
    messages.append({"role": "user", "content": "\n\n".join(part for part in user_parts if part)})
    return messages


def estimate_tokens(messages: Sequence[dict[str, str]]) -> int:
    total = sum(len(message.get("content") or "") for message in messages)
    return int(total / _CHARS_PER_TOKEN)


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------


class RewriteOrchestrator:
    """Drives the chapter loop and owns the ledger."""

    def __init__(
        self,
        workspace: RewriteWorkspace,
        compilation: StoryCompilationWorkspace,
        author: RewriteAuthor,
        *,
        words_per_chapter: int = DEFAULT_WORDS_PER_CHAPTER,
        recent: int = DEFAULT_RECENT_SUMMARIES,
        context_limit: int = CONTEXT_COMPACT_TOKENS,
        on_progress: Any | None = None,
    ) -> None:
        self.workspace = workspace
        self.compilation = compilation
        self.author = author
        self.words_per_chapter = words_per_chapter
        self.recent = recent
        self.context_limit = context_limit
        self.on_progress = on_progress

    # -- inputs ---------------------------------------------------------

    def load_cards(self, limit: int | None = None) -> list[ChapterCard]:
        cards: list[ChapterCard] = []
        for path in sorted(self.compilation.chapter_cards_dir.glob("chapter_*.json")):
            payload = self.compilation.read_json(path)
            if not isinstance(payload, dict):
                continue
            cards.append(ChapterCard.model_validate(payload))
        cards.sort(key=lambda card: card.chapter)
        return cards[:limit] if limit else cards

    def world_profile(self) -> str:
        """The frozen world baseline: setting, cast, and relationship web.

        The compiler writes both per-section files and a merged document; using
        both would duplicate most of the text, so the merged one wins when it
        exists.
        """
        manifest = self.compilation.load_manifest()
        title = str(manifest.get("title") or manifest.get("source_id") or "")
        chunks = [f"# 原作：{title}"] if title else []

        world_dir = self.compilation.world_dir
        merged = world_dir / "world_knowledge.md"
        if merged.exists():
            try:
                chunks.append(merged.read_text(encoding="utf-8").strip())
            except OSError:
                pass
        elif world_dir.exists():
            for path in sorted(world_dir.glob("*.md")):
                try:
                    chunks.append(path.read_text(encoding="utf-8").strip())
                except OSError:
                    continue

        entities = self.compilation.read_json(self.compilation.entities_path, [])
        if isinstance(entities, list) and entities:
            names = [
                str(item.get("name"))
                for item in entities
                if isinstance(item, dict) and item.get("name")
            ]
            if names:
                chunks.append("# 人物与物件\n" + "、".join(names[:200]))

        # The relationship web matters most here: a retelling usually changes
        # exactly these edges, so the baseline has to be visible.
        relationships = self.compilation.read_json(self.compilation.relationships_path, [])
        lines = _relationship_lines(relationships)
        if lines:
            chunks.append("\n".join(lines))

        return "\n\n".join(chunk for chunk in chunks if chunk.strip())

    def brief_profile(self, *, budget: int = BRIEF_PROFILE_BUDGET) -> str:
        """The profile window the brief is written against.

        A brief decides what the retelling changes, and what it changes is
        usually a relationship -- so the relationship baseline has to be in the
        window. Taking the head of the profile instead, which is the obvious
        thing to do, spends the whole budget on setting prose and silently cuts
        the relationship web that sits at the end.
        """
        lines = _relationship_lines(
            self.compilation.read_json(self.compilation.relationships_path, [])
        )
        block = "\n".join(lines)
        profile = self.world_profile()
        head_budget = max(0, budget - len(block) - 2)
        head = profile[:head_budget].rstrip()
        return f"{head}\n\n{block}".strip() if block else head

    def chapter_titles(self, *, limit: int = 120) -> str:
        """The source's chapter list, which the planner schedules against.

        This is what lets the planner notice that a character the reader wants
        early only arrives at chapter nine, and put the divergence somewhere.
        """
        lines = [f"第 {card.chapter} 章《{card.title}》" for card in self.load_cards()[:limit]]
        return "\n".join(lines)

    def source_sample(self, *, budget: int = BRIEF_SAMPLE_BUDGET) -> str:
        """Real source prose, for the brief to judge tone from.

        Outline cards describe what happens; they say nothing about how it is
        written, which is the one thing a style judgement needs.
        """
        payload = self.compilation.read_json(self.compilation.source_path, {})
        chapters = payload.get("chapters") if isinstance(payload, dict) else None
        if not isinstance(chapters, list) or not chapters:
            cards = self.load_cards(1)
            return cards[0].chapter_outline_600 if cards else ""
        sample_parts: list[str] = []
        used = 0
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            text = str(chapter.get("text") or "").strip()
            if not text:
                continue
            take = text[: max(0, budget - used)]
            if not take:
                break
            sample_parts.append(take)
            used += len(take)
            if used >= budget:
                break
        return "\n\n".join(sample_parts)

    # -- entry points ---------------------------------------------------

    async def start(self, instruction: str, *, target: int | None = None) -> RewriteState:
        """Turn one instruction into a brief and a fresh state."""
        cards = self.load_cards(target)
        if not cards:
            raise ValueError("no chapter cards found; compile the source first")
        brief = await self.author.plan_brief(
            instruction, self.brief_profile(), self.source_sample(), self.chapter_titles()
        )
        manifest = self.compilation.load_manifest()
        state = RewriteState(
            source_id=str(manifest.get("source_id") or self.compilation.root.name),
            brief=brief,
            total_chapters=len(self.load_cards()),
            target_chapters=len(cards),
            words_per_chapter=self.words_per_chapter,
        )
        self.workspace.ensure()
        self.workspace.save_state(state)
        _log(
            f"rewrite brief ready: {state.source_id} "
            f"target {state.target_chapters} chapters | premise={brief.premise[:40]!r}"
        )
        return state

    async def run(self, *, target: int | None = None) -> RewriteState:
        """Write every remaining chapter, resuming from the saved state."""
        state = self.workspace.load_state()
        if state is None:
            raise ValueError("no rewrite state; call start() with an instruction first")
        cards = self.load_cards(target or state.target_chapters or None)
        if len(cards) > state.target_chapters:
            # The caller widened the run (e.g. --chapters 6 after starting with 3).
            state.target_chapters = len(cards)
        profile = self.world_profile()
        done = set(state.completed)
        for card in cards:
            if card.chapter in done:
                continue
            try:
                await self.write_one(state, card, profile)
            finally:
                # Persist even on failure: a recorded failure is part of the
                # resumable state, and the ledger may have grown.
                self.workspace.save_state(state)
        return state

    async def write_one(self, state: RewriteState, card: ChapterCard, profile: str) -> ChapterDraft:
        """Write, check, and commit one chapter."""
        attempt_note = ""
        last_violations: list[str] = []
        for attempt in range(1, MAX_CHAPTER_ATTEMPTS + 1):
            messages = build_chapter_messages(state, card, profile, attempt_note=attempt_note)
            if estimate_tokens(messages) > self.context_limit:
                await self.compact(state)
                messages = build_chapter_messages(state, card, profile, attempt_note=attempt_note)
            _log(
                f"chapter {card.chapter}/{state.target_chapters} "
                f"《{card.title}》writing (attempt {attempt}, "
                f"~{estimate_tokens(messages)} tok)"
            )
            prose = await self.author.write_chapter(messages)
            if not prose.strip():
                last_violations = ["模型返回了空正文"]
                attempt_note = "上次返回空正文。请只输出本章正文。"
                continue

            summary, raw_entries = await self.author.extract_chapter(
                card.chapter, card.title, prose, source_outline=_source_outline(card)
            )
            entries = self._make_entries(state, card.chapter, raw_entries)
            accepted, violations = await self.author.verify_chapter(
                summary, [entry.statement for entry in entries], self._ledger_statements(state)
            )
            if not accepted:
                last_violations = violations
                attempt_note = (
                    "上一次的稿子与已确立的事实冲突，被审查拒绝："
                    + "；".join(violations)
                    + "。请在不违背既有台账的前提下重写本章。"
                )
                _log(f"chapter {card.chapter} rejected: {violations}")
                continue

            draft = ChapterDraft(
                chapter=card.chapter,
                title=card.title,
                prose=prose.strip(),
                summary=summary,
                entries=entries,
            )
            self._commit(state, draft)
            return draft

        state.failures[str(card.chapter)] = "；".join(last_violations) or "未知原因"
        _log(f"chapter {card.chapter} failed after {MAX_CHAPTER_ATTEMPTS} attempts")
        raise RewriteConflict(last_violations or ["章节未能通过审查"])

    # -- internals ------------------------------------------------------

    @staticmethod
    def _ledger_statements(state: RewriteState) -> list[str]:
        return [entry.statement for entry in state.ledger]

    @staticmethod
    def _make_entries(
        state: RewriteState, chapter: int, raw: Sequence[dict[str, Any]]
    ) -> list[LedgerEntry]:
        entries: list[LedgerEntry] = []
        for index, item in enumerate(raw, start=1):
            statement = str(item.get("statement") or "").strip()
            if not statement:
                continue
            kind = str(item.get("kind") or "divergence")
            if kind not in {
                "divergence",
                "consequence",
                "character",
                "relationship",
                "object",
                "promise",
            }:
                kind = "divergence"
            entries.append(
                LedgerEntry(
                    entry_id=f"c{chapter:04d}-{index}",
                    chapter=chapter,
                    statement=statement,
                    kind=kind,  # type: ignore[arg-type]
                )
            )
        return entries

    def _commit(self, state: RewriteState, draft: ChapterDraft) -> None:
        self.workspace.write_text(self.workspace.chapter_path(draft.chapter), draft.prose)
        state.ledger.extend(draft.entries)
        state.summaries.append(
            ChapterSummary(
                chapter=draft.chapter,
                title=draft.title,
                summary=draft.summary,
                entries=[entry.entry_id for entry in draft.entries],
            )
        )
        if draft.chapter not in state.completed:
            state.completed.append(draft.chapter)
        state.completed.sort()
        self.workspace.write_json(
            self.workspace.meta_path(draft.chapter),
            {
                "chapter": draft.chapter,
                "title": draft.title,
                "summary": draft.summary,
                "entries": [entry.model_dump(mode="json") for entry in draft.entries],
                "words": len(draft.prose),
            },
        )
        _log(
            f"chapter {draft.chapter} done ({len(draft.prose)} chars, "
            f"{len(draft.entries)} ledger entries, ledger size {len(state.ledger)})"
        )

    def export_book(self, state: RewriteState | None = None) -> Path:
        """Assemble the written chapters into one readable file.

        A retelling is something you read; a directory of chapter files is not.
        The export carries the premise and the progress so a partial book is
        still honestly labelled.
        """
        current = state or self.workspace.load_state()
        if current is None:
            raise ValueError("no rewrite state to export")
        titles = {card.chapter: card.title for card in self.load_cards()}
        premise = current.brief.premise or current.brief.instruction
        lines = [
            f"# {current.source_id}·仿写",
            "",
            f"> 改写前提：{premise}",
            f"> 已完成：{len(current.completed)}/{current.target_chapters} 章",
            "",
            "## 目录",
            "",
        ]
        for number in current.completed:
            lines.append(f"{number}. {titles.get(number, f'第 {number} 章')}")
        lines.append("")
        for number in current.completed:
            lines.extend(
                [
                    f"## {titles.get(number, f'第 {number} 章')}",
                    "",
                    self.workspace.chapter_text(number),
                    "",
                ]
            )
        path = self.workspace.root / "book.md"
        self.workspace.write_text(path, "\n".join(lines))
        _log(f"book exported: {path} ({len(current.completed)} chapters)")
        return path

    def export_report(self, state: RewriteState | None = None) -> Path:
        """An acceptance report: what was written, and what must stay true.

        Drift is invisible in a finished draft; it becomes visible against the
        ledger. This is the artefact a human checks the book against, and the
        reason the ledger is kept as prose statements rather than ids.
        """
        current = state or self.workspace.load_state()
        if current is None:
            raise ValueError("no rewrite state to report on")
        titles = {card.chapter: card.title for card in self.load_cards()}
        lines = [
            f"# {current.source_id}·仿写验收报告",
            "",
            f"- 改写前提：{current.brief.premise or current.brief.instruction}",
            f"- 进度：{len(current.completed)}/{current.target_chapters}",
            f"- 每章目标：{current.words_per_chapter} 字",
            f"- 事实台账：{len(current.ledger)} 条",
        ]
        if current.brief.divergences:
            lines.append("- 必须改变：" + "；".join(current.brief.divergences))
        if current.brief.invariants:
            lines.append("- 必须保留：" + "；".join(current.brief.invariants))

        if current.brief.plan:
            lines.extend(["", "## 改写计划（每章兑现情况需人工核对）", ""])
            lines.extend(
                f"- 第 {point.chapter} 章起：{point.change}" for point in current.brief.plan
            )

        lines.extend(
            [
                "",
                "## 逐章",
                "",
                "| 章 | 标题 | 字数 | 新增台账 | 摘要 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for summary in current.summaries:
            meta = self.workspace.read_json(self.workspace.meta_path(summary.chapter), {}) or {}
            words = meta.get("words") or len(self.workspace.chapter_text(summary.chapter))
            title = titles.get(summary.chapter) or summary.title
            brief_summary = summary.summary.replace("\n", " ")[:80]
            lines.append(
                f"| {summary.chapter} | {title} | {words} | "
                f"{len(summary.entries)} | {brief_summary} |"
            )

        lines.extend(["", "## 事实台账（此后不得推翻）", ""])
        if current.ledger:
            lines.extend(
                f"- 第 {entry.chapter} 章｜{entry.kind}｜{entry.statement}"
                for entry in current.ledger
            )
        else:
            lines.append("（无）")

        if current.failures:
            lines.extend(["", "## 失败章节", ""])
            for chapter in sorted(current.failures, key=lambda value: int(value)):
                lines.append(f"- 第 {chapter} 章：{current.failures[chapter]}")

        path = self.workspace.root / "report.md"
        self.workspace.write_text(path, "\n".join(lines))
        _log(f"report exported: {path}")
        return path

    async def compact(self, state: RewriteState) -> None:
        """Roll the oldest chapter summaries into one, keeping the ledger intact."""
        keep = self.recent
        if len(state.summaries) <= keep:
            return
        old = state.summaries[:-keep]
        if not old:
            return
        joined = [f"第 {item.chapter} 章《{item.title}》：{item.summary}" for item in old]
        rolled = await self.author.compact_summaries(joined)
        state.rolling_summary = (
            (state.rolling_summary.strip() + "\n\n" + rolled).strip()
            if state.rolling_summary.strip()
            else rolled
        )
        state.summaries = state.summaries[-keep:]
        _log(f"compacted {len(old)} chapter summaries; ledger kept at {len(state.ledger)}")


__all__ = [
    "BRIEF_CONTRACT",
    "CHAPTER_CONTRACT",
    "CONTEXT_COMPACT_TOKENS",
    "ChapterDraft",
    "ChapterSummary",
    "FakeRewriteAuthor",
    "LedgerEntry",
    "OpenAIRewriteAuthor",
    "RewriteAuthor",
    "RewriteBrief",
    "RewriteConflict",
    "RewriteOrchestrator",
    "RewriteState",
    "RewriteWorkspace",
    "build_chapter_messages",
    "estimate_tokens",
    "instruction_marker",
]
