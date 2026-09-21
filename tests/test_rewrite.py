"""Rewrite mode: one instruction in, a same-scale retelling out.

The tests here pin the anti-drift invariants, not the prose:

* the rewrite premise must be present in every chapter prompt;
* the ledger must be complete in every chapter prompt (it is never compacted);
* a chapter that contradicts the ledger is not published;
* work resumes without recomputing finished chapters.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trpg_runtime.story.decomposer import (
    ChapterCard,
    StoryCompilationWorkspace,
)
from trpg_runtime.story.rewrite import (
    ChapterDraft,
    FakeRewriteAuthor,
    LedgerEntry,
    RewriteConflict,
    RewriteOrchestrator,
    RewriteWorkspace,
    build_chapter_messages,
    estimate_tokens,
    instruction_marker,
)

INSTRUCTION = "如果阿飞早点认识林仙儿"


def make_compilation(tmp_path: Path, chapters: int = 5) -> StoryCompilationWorkspace:
    comp = StoryCompilationWorkspace(tmp_path / "compiled")
    comp.ensure()
    comp.save_manifest(
        {"source_id": "demo", "title": "测试原作", "status": "complete", "total_chapters": chapters}
    )
    for number in range(1, chapters + 1):
        card = ChapterCard(
            chapter=number,
            title=f"第{number}章",
            chapter_outline_600=f"第 {number} 章大纲：人物遭遇冲突并作出选择。",
            story_line=f"第 {number} 章情节线",
            highlights=[f"要点{number}"],
        )
        comp.write_json(
            comp.chapter_cards_dir / f"chapter_{number:04d}.json", card.model_dump(mode="json")
        )
    (comp.world_dir / "world.md").write_text("世界设定：这是一座多雨的城。", encoding="utf-8")
    comp.write_json(comp.entities_path, [{"name": "阿飞"}, {"name": "林仙儿"}])
    return comp


class RecordingAuthor(FakeRewriteAuthor):
    """Fake author that keeps every chapter prompt for assertions."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.chapter_prompts: list[list[dict[str, str]]] = []

    async def write_chapter(self, messages):
        self.chapter_prompts.append([dict(message) for message in messages])
        return await super().write_chapter(messages)


def make_orchestrator(tmp_path, *, chapters=5, **kwargs):
    comp = make_compilation(tmp_path, chapters=chapters)
    workspace = RewriteWorkspace(tmp_path / "rewrite")
    author = kwargs.pop("author", None) or RecordingAuthor()
    orchestrator = RewriteOrchestrator(workspace, comp, author, **kwargs)
    return comp, workspace, author, orchestrator


# --- brief ------------------------------------------------------------------


def test_start_builds_and_persists_a_brief(tmp_path):
    _, workspace, _, orchestrator = make_orchestrator(tmp_path, chapters=3)
    state = asyncio.run(orchestrator.start(INSTRUCTION))
    assert state.brief.instruction == INSTRUCTION
    assert state.brief.premise
    assert state.target_chapters == 3
    assert state.total_chapters == 3

    reloaded = workspace.load_state()
    assert reloaded is not None
    assert reloaded.brief.instruction == INSTRUCTION


def test_start_requires_compiled_cards(tmp_path):
    empty = StoryCompilationWorkspace(tmp_path / "empty")
    empty.ensure()
    workspace = RewriteWorkspace(tmp_path / "rw")
    orchestrator = RewriteOrchestrator(workspace, empty, FakeRewriteAuthor())
    with pytest.raises(ValueError, match="compile the source first"):
        asyncio.run(orchestrator.start(INSTRUCTION))


# --- the anti-drift invariants ---------------------------------------------


def test_every_chapter_prompt_carries_the_premise(tmp_path):
    """A retelling that loses the premise has failed, whatever the prose."""
    _, _, author, orchestrator = make_orchestrator(tmp_path, chapters=4)
    asyncio.run(orchestrator.start(INSTRUCTION))
    state = asyncio.run(orchestrator.run())

    assert len(author.chapter_prompts) == 4
    for prompt in author.chapter_prompts:
        assert instruction_marker(prompt) == state.brief.premise


def test_every_chapter_prompt_carries_the_whole_ledger(tmp_path):
    """The ledger is the anchor: it must never be summarised away."""
    _, _, author, orchestrator = make_orchestrator(tmp_path, chapters=5)
    asyncio.run(orchestrator.start(INSTRUCTION))
    state = asyncio.run(orchestrator.run())

    statements = [entry.statement for entry in state.ledger]
    assert len(statements) == 5

    # the last chapter sees every entry established before it -- in full
    last_prompt = "\n".join(message["content"] for message in author.chapter_prompts[-1])
    for statement in statements[:4]:
        assert statement in last_prompt
    # and not the one it is about to establish itself
    assert statements[4] not in last_prompt


def test_ledger_only_grows(tmp_path):
    _, workspace, _, orchestrator = make_orchestrator(tmp_path, chapters=4)
    asyncio.run(orchestrator.start(INSTRUCTION))
    sizes: list[int] = []
    state = workspace.load_state()
    assert state is not None
    cards = orchestrator.load_cards()
    profile = orchestrator.world_profile()
    for card in cards:
        asyncio.run(orchestrator.write_one(state, card, profile))
        sizes.append(len(state.ledger))
    assert sizes == sorted(sizes)
    assert sizes[-1] == 4


def test_conflicting_chapter_is_retried_then_published(tmp_path):
    author = RecordingAuthor(conflict_on="第3章")
    _, workspace, _, orchestrator = make_orchestrator(tmp_path, chapters=3, author=author)
    asyncio.run(orchestrator.start(INSTRUCTION))
    state = asyncio.run(orchestrator.run())

    # chapter 3 was rejected once and then rewritten
    assert author.verify_calls >= 4
    assert 3 in state.completed
    assert state.failures == {}
    assert workspace.chapter_text(3).strip()


def test_persistent_conflict_is_recorded_not_published(tmp_path):
    class AlwaysConflicting(RecordingAuthor):
        async def verify_chapter(self, summary, statements, ledger):
            self.verify_calls += 1
            return False, ["永久冲突"]

    author = AlwaysConflicting()
    _, workspace, _, orchestrator = make_orchestrator(tmp_path, chapters=2, author=author)
    asyncio.run(orchestrator.start(INSTRUCTION))
    with pytest.raises(RewriteConflict):
        asyncio.run(orchestrator.run())
    state = workspace.load_state()
    assert state is not None
    assert "1" in state.failures
    assert workspace.chapter_text(1) == ""


# --- resume -----------------------------------------------------------------


def test_run_resumes_without_recomputing_finished_chapters(tmp_path):
    _, workspace, author, orchestrator = make_orchestrator(tmp_path, chapters=4)
    asyncio.run(orchestrator.start(INSTRUCTION))
    state = workspace.load_state()
    assert state is not None
    cards = orchestrator.load_cards()
    profile = orchestrator.world_profile()

    asyncio.run(orchestrator.write_one(state, cards[0], profile))
    asyncio.run(orchestrator.write_one(state, cards[1], profile))
    workspace.save_state(state)
    assert len(author.chapter_prompts) == 2

    resumed = asyncio.run(orchestrator.run())
    assert resumed.completed == [1, 2, 3, 4]
    # only the two missing chapters were written
    assert len(author.chapter_prompts) == 4


def test_reload_keeps_ledger_and_summaries(tmp_path):
    _, workspace, _, orchestrator = make_orchestrator(tmp_path, chapters=3)
    asyncio.run(orchestrator.start(INSTRUCTION))
    asyncio.run(orchestrator.run())
    reloaded = workspace.load_state()
    assert reloaded is not None
    assert len(reloaded.ledger) == 3
    assert [item.chapter for item in reloaded.summaries] == [1, 2, 3]


def test_widening_the_target_updates_state(tmp_path):
    """Starting with 2 chapters then asking for 4 must not report 4/2."""
    _, workspace, author, orchestrator = make_orchestrator(tmp_path, chapters=4)
    state = asyncio.run(orchestrator.start(INSTRUCTION, target=2))
    assert state.target_chapters == 2
    asyncio.run(orchestrator.run(target=4))

    reloaded = workspace.load_state()
    assert reloaded is not None
    assert reloaded.target_chapters == 4
    assert reloaded.completed == [1, 2, 3, 4]


def test_export_book_assembles_a_readable_volume(tmp_path):
    """A retelling should come out as one book, honestly labelled."""
    _, workspace, _, orchestrator = make_orchestrator(tmp_path, chapters=3)
    asyncio.run(orchestrator.start(INSTRUCTION, target=2))
    state = asyncio.run(orchestrator.run(target=2))

    path = orchestrator.export_book(state)
    assert path == workspace.root / "book.md"
    text = path.read_text(encoding="utf-8")

    assert text.startswith("# demo·仿写")
    assert INSTRUCTION in text  # the premise travels with the book
    assert "已完成：2/2 章" in text
    assert "## 目录" in text
    # every written chapter appears as a heading, in order
    assert text.index("## 第1章") < text.index("## 第2章")
    assert workspace.chapter_text(1) in text
    assert workspace.chapter_text(2) in text


def test_export_book_reports_partial_progress(tmp_path):
    _, workspace, _, orchestrator = make_orchestrator(tmp_path, chapters=4)
    asyncio.run(orchestrator.start(INSTRUCTION))
    state = asyncio.run(orchestrator.run(target=4))
    state.completed = [1, 2]
    text = orchestrator.export_book(state).read_text(encoding="utf-8")
    assert "已完成：2/4 章" in text
    assert "## 第3章" not in text


# --- compaction -------------------------------------------------------------


def test_compaction_rolls_summaries_and_keeps_the_ledger(tmp_path):
    _, workspace, author, orchestrator = make_orchestrator(tmp_path, chapters=6, recent=2)
    asyncio.run(orchestrator.start(INSTRUCTION))
    state = asyncio.run(orchestrator.run())
    assert len(state.summaries) == 6
    ledger_before = len(state.ledger)

    asyncio.run(orchestrator.compact(state))
    assert len(state.summaries) == 2
    assert state.rolling_summary.startswith("（压缩梗概）")
    # the ledger is untouched by compaction: that is the whole point
    assert len(state.ledger) == ledger_before
    assert workspace.load_state() is not None


def test_tight_context_limit_triggers_compaction(tmp_path):
    _, _, _, orchestrator = make_orchestrator(tmp_path, chapters=6, recent=1, context_limit=60)
    asyncio.run(orchestrator.start(INSTRUCTION))
    state = asyncio.run(orchestrator.run())
    assert state.rolling_summary
    assert len(state.summaries) <= 2
    assert len(state.ledger) == 6


# --- context shape ----------------------------------------------------------


def test_chapter_prompt_orders_history_then_ledger_then_task(tmp_path):
    comp = make_compilation(tmp_path, chapters=2)
    card = ChapterCard(
        chapter=2,
        title="第二章",
        chapter_outline_600="第二章大纲",
        story_line="第二章情节线",
    )
    workspace = RewriteWorkspace(tmp_path / "rw")
    orchestrator = RewriteOrchestrator(workspace, comp, FakeRewriteAuthor(), recent=1)
    state = asyncio.run(orchestrator.start(INSTRUCTION))
    state.ledger.append(LedgerEntry(entry_id="c0001-1", chapter=1, statement="第一章的偏离"))
    state.rolling_summary = "早年梗概"
    messages = build_chapter_messages(state, card, orchestrator.world_profile(), recent=1)

    system = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "改写前提" in system
    assert "长篇仿写契约" in system

    body = messages[1]["content"]
    assert body.index("早年情节梗概") < body.index("事实台账")
    assert body.index("事实台账") < body.index("本章任务")
    assert "第一章的偏离" in body
    assert "第二章大纲" in body
    assert "目标长度" in body


def test_estimate_tokens_is_monotonic(tmp_path):
    short = [{"role": "user", "content": "短"}]
    long = [{"role": "user", "content": "长" * 400}]
    assert estimate_tokens(long) > estimate_tokens(short)


def test_chapter_files_and_meta_are_written(tmp_path):
    _, workspace, _, orchestrator = make_orchestrator(tmp_path, chapters=2)
    asyncio.run(orchestrator.start(INSTRUCTION))
    asyncio.run(orchestrator.run())
    assert workspace.chapter_path(1).exists()
    assert workspace.meta_path(2).exists()
    payload = workspace.read_json(workspace.meta_path(1), {})
    assert payload["chapter"] == 1
    assert payload["words"] > 0
    assert payload["entries"]


def test_draft_model_round_trip():
    draft = ChapterDraft(
        chapter=1,
        title="第一章",
        prose="正文",
        summary="摘要",
        entries=[LedgerEntry(entry_id="c0001-1", chapter=1, statement="偏离")],
    )
    again = ChapterDraft.model_validate(draft.model_dump(mode="json"))
    assert again == draft
