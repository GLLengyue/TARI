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
    "- 按本章大纲推进：可以增补细节、对话与场景，但不要跳过应当发生的事。\n"
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
    "- tone：语体要求，若读者没提就沿用原作的语体。\n"
    "只返回 JSON："
    '{"premise":"...","divergences":["..."],"invariants":["..."],"tone":"..."}'
)

EXTRACT_CONTRACT = (
    "给你一章正文。请提炼它的索引信息，供后续章节使用。\n"
    "- summary：这一章发生了什么，300 字以内，只写事实与因果，不写评价；\n"
    "- entries：本章确立的、与原作不同且此后必须成立的既定事实。"
    "只记录会影响后续连续性的（人物状态、关系、承诺、物品归属、关键事件），"
    "每条一句话。若本章没有新的偏离，返回空列表。\n"
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


class RewriteBrief(BaseModel):
    """The reader's single sentence, expanded into workable constraints."""

    instruction: str
    premise: str = ""
    divergences: list[str] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    tone: str = ""


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
    async def plan_brief(self, instruction: str, world_profile: str, sample: str) -> RewriteBrief:
        raise NotImplementedError

    @abstractmethod
    async def write_chapter(self, messages: Sequence[dict[str, str]]) -> str:
        raise NotImplementedError

    @abstractmethod
    async def extract_chapter(
        self, chapter: int, title: str, prose: str
    ) -> tuple[str, list[dict[str, Any]]]:
        """Return (summary, raw entries) for one written chapter."""
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

    async def plan_brief(self, instruction: str, world_profile: str, sample: str) -> RewriteBrief:
        payload = await self._client.complete_json(
            [
                {"role": "system", "content": BRIEF_CONTRACT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "读者的要求": instruction,
                            "原作世界档案": world_profile[:4000],
                            "原作开头样本": sample[:2000],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=1200,
        )
        return RewriteBrief(
            instruction=instruction,
            premise=str(payload.get("premise") or instruction),
            divergences=[str(item) for item in payload.get("divergences") or []],
            invariants=[str(item) for item in payload.get("invariants") or []],
            tone=str(payload.get("tone") or ""),
        )

    async def write_chapter(self, messages: Sequence[dict[str, str]]) -> str:
        text = await self._client.complete_text(list(messages))
        return text.strip()

    async def extract_chapter(
        self, chapter: int, title: str, prose: str
    ) -> tuple[str, list[dict[str, Any]]]:
        payload = await self._client.complete_json(
            [
                {"role": "system", "content": EXTRACT_CONTRACT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"chapter": chapter, "title": title, "正文": prose},
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

    async def plan_brief(self, instruction: str, world_profile: str, sample: str) -> RewriteBrief:
        return RewriteBrief(
            instruction=instruction,
            premise=instruction,
            divergences=[f"按读者的要求改变：{instruction}"],
            invariants=["人物性格底色不变"],
            tone="沿用原作语体",
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
        self, chapter: int, title: str, prose: str
    ) -> tuple[str, list[dict[str, Any]]]:
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


def _history_block(state: RewriteState, recent: int) -> str:
    chunks: list[str] = []
    if state.rolling_summary.strip():
        chunks.append("# 早年情节梗概（压缩）\n" + state.rolling_summary.strip())
    recent_summaries = state.summaries[-recent:] if recent > 0 else []
    if recent_summaries:
        lines = ["# 最近章节（逐章摘要）"]
        lines.extend(
            f"- 第 {item.chapter} 章《{item.title}》：{item.summary}" for item in recent_summaries
        )
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def build_chapter_messages(
    state: RewriteState,
    card: ChapterCard,
    world_profile: str,
    *,
    recent: int = DEFAULT_RECENT_SUMMARIES,
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
        _history_block(state, recent),
        _ledger_block(state.ledger),
        "# 本章任务\n"
        f"写作第 {card.chapter} 章《{card.title}》。\n"
        f"目标长度：{state.words_per_chapter} 字左右。\n"
        f"原作本章梗概（供参考，改写后可不同）：{card.chapter_outline_600.strip()}\n"
        f"本章情节线：{card.story_line.strip() or '（未提供）'}\n"
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
        if isinstance(relationships, list) and relationships:
            lines = ["# 人物关系（原作基线；本次改写可能改变其中若干条）"]
            for item in relationships[:RELATIONSHIP_BASELINE]:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or "").strip()
                description = str(item.get("description") or "").strip()
                if not label and not description:
                    continue
                lines.append(
                    f"- {label}：{description}"
                    if label and description
                    else f"- {label or description}"
                )
            if len(lines) > 1:
                chunks.append("\n".join(lines))

        return "\n\n".join(chunk for chunk in chunks if chunk.strip())

    # -- entry points ---------------------------------------------------

    async def start(self, instruction: str, *, target: int | None = None) -> RewriteState:
        """Turn one instruction into a brief and a fresh state."""
        cards = self.load_cards(target)
        if not cards:
            raise ValueError("no chapter cards found; compile the source first")
        profile = self.world_profile()
        sample = cards[0].chapter_outline_600
        brief = await self.author.plan_brief(instruction, profile, sample)
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
            messages = build_chapter_messages(
                state, card, profile, recent=self.recent, attempt_note=attempt_note
            )
            if estimate_tokens(messages) > self.context_limit:
                await self.compact(state)
                messages = build_chapter_messages(
                    state, card, profile, recent=self.recent, attempt_note=attempt_note
                )
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
                card.chapter, card.title, prose
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
