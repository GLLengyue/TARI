from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from trpg_runtime.llm import LLMSettings
from trpg_runtime.narrative import compile_bundle
from trpg_runtime.story import load_bundle
from trpg_runtime.story.decomposer import (
    SOURCE_PLAN_VERSION,
    SourceStructurePlan,
    _materialise_source_document,
    _normalise_source_plan,
)


def _fake_source_plan(user_prompt: str) -> str:
    import re as _re

    sha = _re.search(r"SHA-256（必须原样回传）：([0-9a-f]+)", user_prompt)
    total = _re.search(r"总行数。+：(\d+)", user_prompt)
    sha_value = sha.group(1) if sha else ""
    total_lines = int(total.group(1)) if total else 0
    heading_lines = sorted(
        {
            int(num)
            for num, text in _re.findall(r'"line":\s*(\d+),\s*"text":\s*"([^"]+)"', user_prompt)
            if text.startswith("##")
        }
    )
    if not heading_lines:
        return json.dumps(
            {
                "source_sha256": sha_value,
                "total_lines": total_lines,
                "content_start_line": 1,
                "content_end_line": max(total_lines, 1),
                "excluded_ranges": [],
                "chapters": [
                    {
                        "ordinal": 1,
                        "title": "Chapter 1",
                        "start_line": 1,
                        "end_line": max(total_lines, 1),
                        "reason": "single chapter",
                    }
                ],
            },
            ensure_ascii=False,
        )
    boundaries = heading_lines + [total_lines + 1]
    chapters: list[dict[str, object]] = []
    for index, (start, next_start) in enumerate(
        zip(boundaries, boundaries[1:], strict=False), start=1
    ):
        end_line = max(int(start), int(next_start) - 1)
        chapters.append(
            {
                "ordinal": index,
                "title": f"Chapter {index}",
                "start_line": int(start),
                "end_line": end_line,
                "reason": "## markdown heading",
            }
        )
    return json.dumps(
        {
            "source_sha256": sha_value,
            "total_lines": total_lines,
            "content_start_line": chapters[0]["start_line"],
            "content_end_line": chapters[-1]["end_line"],
            "excluded_ranges": [
                {
                    "start_line": 1,
                    "end_line": chapters[0]["start_line"] - 1,
                    "reason": "preamble",
                }
            ]
            if chapters[0]["start_line"] > 1
            else [],
            "chapters": chapters,
        },
        ensure_ascii=False,
    )


class FakeCompilerAuthor:
    settings = LLMSettings(
        provider="fake",
        base_url="",
        api_key="",
        model="fake-compiler",
    )

    async def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "长篇小说源文档结构分析员" in system:
            return _fake_source_plan(user)
        if "长篇小说拆书编辑" in system:
            chapter_match = re.search(r"全书第\s*(\d+)\s*章", user)
            chapter = int(chapter_match.group(1)) if chapter_match else 1
            return json.dumps(
                {
                    "chapter": chapter,
                    "title": f"Chapter {chapter}",
                    "chapter_outline_600": f"A concrete outline for chapter {chapter}.",
                    "chapter_rhythm": {
                        "core_content": "arrival -> pressure",
                        "emotion_tone": "curiosity -> resolve",
                        "beat_detail": "the clue becomes actionable",
                    },
                    "story_line": f"Ari investigates chapter {chapter}",
                    "highlights": [f"clue-{chapter}"],
                    "entities": [
                        {
                            "name": "Ari",
                            "kind": "character",
                            "description": "The viewpoint investigator.",
                        },
                        {
                            "name": "Lantern Gate",
                            "kind": "place",
                            "description": "A gate tied to the source mystery.",
                        },
                    ],
                    "relationships": [
                        {
                            "source": "Ari",
                            "target": "Lantern Gate",
                            "label": "investigates",
                            "description": "Ari examines the gate.",
                        }
                    ],
                    "facts": [
                        {
                            "content": f"The gate yields clue {chapter}.",
                            "visibility": "public",
                            "known_by": ["Ari"],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        if "专业小说结构编辑" in system:
            return json.dumps(
                {
                    "completed_segments": [
                        {
                            "title": "The Gate Investigation",
                            "start_chapter": 1,
                            "end_chapter": 2,
                            "narrative_function": "Turn the mystery into a concrete investigation.",
                            "boundary_reason": (
                                "The second clue closes the available source window."
                            ),
                            "structure": "arrival -> pressure -> clue",
                            "protagonist_action": "Ari investigates the gate.",
                            "emotion_rhythm": "curiosity -> resolve",
                            "satisfaction_point": "The hidden clue is recovered.",
                            "character_changes": "Ari commits to the investigation.",
                            "gains_costs": "A clue is gained; danger increases.",
                            "foreshadowing": "The gate conceals a larger history.",
                        }
                    ],
                    "carryover_reason": "无",
                },
                ensure_ascii=False,
            )
        if "小说世界资料架构师" in system:
            return "\n\n".join(
                [
                    "# 世界观\n\nThe gate is an old boundary.",
                    "# 力量体系\n\n无",
                    "# 关键人物\n\nAri investigates the gate.",
                    "# 势力描述\n\n无",
                    "# 故事主线\n\nAri follows the recovered clues.",
                    "# 关键物品\n\nThe lantern is evidence.",
                    "# 技能体系\n\n无",
                ]
            )
        if "完整小说大纲整理员" in system:
            return "# 定位与核心设定\n\nA source-preserving mystery."
        if "长篇小说结构整理员" in system:
            return "# 概览\n\nAri turns the clue into an investigation."
        raise AssertionError(f"unexpected compiler prompt: {system}")

    async def aclose(self) -> None:
        return None


class NoCallCompilerAuthor(FakeCompilerAuthor):
    async def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        raise AssertionError("cached compilation unexpectedly called the author")


def test_compile_bundle_writes_auditable_outputs_and_resumes_from_cache(tmp_path: Path):
    source = tmp_path / "lantern.md"
    source.write_text(
        "# The Lantern Gate\n\n"
        "## Arrival\n\nAri reaches the gate at dusk.\n\n"
        "## The Clue\n\nAri finds a mark beneath the lantern.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "compiled"
    published_world = tmp_path / "published-world.json"

    result = compile_bundle(
        source,
        workspace,
        story_id="lantern-compiled",
        title="Compiled Lantern",
        author=FakeCompilerAuthor(),
        parallelism=2,
        window_chapters=2,
        max_arc_chapters=2,
        world_batch_chapters=2,
        publish_world_path=published_world,
    )

    assert result.source_id == "lantern-compiled"
    assert result.chapter_count == 2
    assert result.card_count == 2
    assert result.arc_count == 1
    assert result.compiler == "tari_harnessnovel_compatible"
    assert Path(result.bundle_path).is_file()
    assert Path(result.manifest_path).is_file()
    assert published_world.is_file()

    bundle = load_bundle(result.bundle_path)
    assert bundle.title == "Compiled Lantern"
    assert bundle.source.source_refs == [
        "lantern-compiled:chapter:001",
        "lantern-compiled:chapter:002",
    ]
    assert bundle.entities
    assert bundle.canon_facts
    assert bundle.relationships
    assert bundle.story_beats

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["stages"] == {
        "source_plan": True,
        "source_plan_version": 1,
        "chapter_cards": True,
        "chapter_cards_completed": 2,
        "story_arcs": True,
        "story_arcs_completed_through": 2,
        "world_knowledge": True,
        "world_cards_completed": 1,
        "world_merge_completed": 1,
        "structures": True,
        "volume_structures_completed": 1,
        "novel_outline": True,
    }

    assert manifest["settings"]["window_chapters"] == 2
    assert manifest["settings_fingerprint"]

    cached = compile_bundle(
        source,
        workspace,
        story_id="lantern-compiled",
        title="Compiled Lantern",
        author=NoCallCompilerAuthor(),
        parallelism=2,
        window_chapters=2,
        max_arc_chapters=2,
        world_batch_chapters=2,
    )
    assert cached.bundle_path == result.bundle_path
    assert cached.manifest_path == result.manifest_path


def test_compile_cache_requires_rebuild_after_source_or_settings_changes(
    tmp_path: Path,
):
    source = tmp_path / "lantern.md"
    source.write_text(
        "# The Lantern Gate\n\n"
        "## Arrival\n\nAri reaches the gate at dusk.\n\n"
        "## The Clue\n\nAri finds a mark beneath the lantern.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "compiled"
    compile_bundle(
        source,
        workspace,
        story_id="lantern-compiled",
        author=FakeCompilerAuthor(),
        parallelism=2,
        window_chapters=2,
        max_arc_chapters=2,
        world_batch_chapters=2,
    )

    with pytest.raises(ValueError, match="compilation settings changed"):
        compile_bundle(
            source,
            workspace,
            story_id="lantern-compiled",
            author=NoCallCompilerAuthor(),
            parallelism=1,
            window_chapters=2,
            max_arc_chapters=2,
            world_batch_chapters=2,
        )

    source.write_text(
        source.read_text(encoding="utf-8") + "\nA new mark appears.\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="source changed"):
        compile_bundle(
            source,
            workspace,
            story_id="lantern-compiled",
            author=NoCallCompilerAuthor(),
            parallelism=2,
            window_chapters=2,
            max_arc_chapters=2,
            world_batch_chapters=2,
        )

    rebuilt = compile_bundle(
        source,
        workspace,
        story_id="lantern-compiled",
        author=FakeCompilerAuthor(),
        parallelism=1,
        window_chapters=2,
        max_arc_chapters=2,
        world_batch_chapters=2,
        rebuild=True,
    )
    assert rebuilt.chapter_count == 2
    assert json.loads(Path(rebuilt.manifest_path).read_text(encoding="utf-8"))["settings"][
        "parallelism"
    ] == 1


def _plan_payload(**overrides):
    payload = {
        "source_sha256": "deadbeef" * 8,
        "total_lines": 20,
        "content_start_line": 4,
        "content_end_line": 18,
        "excluded_ranges": [
            {"start_line": 1, "end_line": 3, "reason": "metadata"}
        ],
        "chapters": [
            {
                "ordinal": 1,
                "title": "Chapter 1",
                "start_line": 4,
                "end_line": 10,
                "reason": "first chapter",
            },
            {
                "ordinal": 2,
                "title": "Chapter 2",
                "start_line": 11,
                "end_line": 18,
                "reason": "second chapter",
            },
        ],
    }
    payload.update(overrides)
    return payload


def test_normalise_source_plan_accepts_well_formed_payload():
    plan = _normalise_source_plan(_plan_payload(), payload_sha := "deadbeef" * 8, 20)
    assert isinstance(plan, SourceStructurePlan)
    assert plan.plan_version == SOURCE_PLAN_VERSION
    assert plan.source_sha256 == payload_sha
    assert [chapter.ordinal for chapter in plan.chapters] == [1, 2]
    assert plan.chapters[1].start_line == 11
    assert plan.chapters[1].end_line == 18


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda p: p.update({"source_sha256": "cafef00d" * 8}),
            id="wrong_sha",
        ),
        pytest.param(
            lambda p: p["chapters"].append(
                {
                    "ordinal": 3,
                    "title": "Chapter 3",
                    "start_line": 8,
                    "end_line": 12,
                    "reason": "overlaps",
                }
            ),
            id="overlapping_chapters",
        ),
        pytest.param(
            lambda p: p["chapters"][0].update({"end_line": 25}),
            id="end_line_past_total",
        ),
        pytest.param(
            lambda p: p["chapters"][0].update({"start_line": 11, "end_line": 11}),
            id="start_after_previous_end",
        ),
        pytest.param(
            lambda p: p.update({"content_start_line": 12, "content_end_line": 18}),
            id="chapter_before_content_window",
        ),
        pytest.param(
            lambda p: p.update({"chapters": []}),
            id="empty_chapters",
        ),
    ],
)
def test_normalise_source_plan_rejects_invalid_payloads(mutate):
    payload = _plan_payload()
    mutate(payload)
    with pytest.raises(ValueError):
        _normalise_source_plan(payload, "deadbeef" * 8, 20)


def test_materialise_source_document_marks_empty_chapter_as_error(tmp_path):
    source = tmp_path / "odyssey.txt"
    raw_text = (
        "Preamble line 1\n"
        "Preamble line 2\n"
        "Preamble line 3\n"
        "Chapter 1 heading\n"
        "Body line for chapter 1\n"
        "Chapter 2 heading\n"
        "Body line for chapter 2\n"
    )
    source.write_text(raw_text, encoding="utf-8")
    plan = SourceStructurePlan.model_validate(
        _plan_payload(
            source_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            total_lines=raw_text.count("\n") + 1,
            content_start_line=1,
            content_end_line=raw_text.count("\n") + 1,
            chapters=[
                {
                    "ordinal": 1,
                    "title": "Chapter 1",
                    "start_line": 4,
                    "end_line": 5,
                    "reason": "",
                },
                {
                    "ordinal": 2,
                    "title": "Chapter 2",
                    "start_line": 6,
                    "end_line": 7,
                    "reason": "",
                },
            ],
        )
    )
    document = _materialise_source_document(
        source,
        raw_text.encode("utf-8"),
        "odyssey",
        None,
        "en",
        plan,
    )
    assert [chapter.ordinal for chapter in document.chapters] == [1, 2]
    assert document.chapters[0].title == "Chapter 1"
    assert "Body line for chapter 1" in document.chapters[0].text

    empty_plan = SourceStructurePlan.model_validate(
        _plan_payload(
            source_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            total_lines=raw_text.count("\n") + 1,
            content_start_line=1,
            content_end_line=raw_text.count("\n") + 1,
            chapters=[
                {
                    "ordinal": 1,
                    "title": "Chapter 1",
                    "start_line": 4,
                    "end_line": 5,
                    "reason": "",
                },
                {
                    "ordinal": 2,
                    "title": "Empty Chapter",
                    "start_line": 8,
                    "end_line": 8,
                    "reason": "",
                },
            ],
        )
    )
    with pytest.raises(ValueError, match="empty chapter"):
        _materialise_source_document(
            source,
            raw_text.encode("utf-8"),
            "odyssey",
            None,
            "en",
            empty_plan,
        )


def test_compile_pipeline_uses_source_plan_stage(tmp_path):
    source = tmp_path / "lantern.md"
    source.write_text(
        "# The Lantern Gate\n\n"
        "## Arrival\n\nAri reaches the gate at dusk.\n\n"
        "## The Clue\n\nAri finds a mark beneath the lantern.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "compiled"
    result = compile_bundle(
        source,
        workspace,
        story_id="lantern-compiled",
        title="Compiled Lantern",
        author=FakeCompilerAuthor(),
        parallelism=2,
        window_chapters=2,
        max_arc_chapters=2,
        world_batch_chapters=2,
    )
    plan_path = workspace / "source_plan.json"
    assert plan_path.is_file()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["plan_version"] == SOURCE_PLAN_VERSION
    assert plan["source_sha256"]
    assert len(plan["chapters"]) == 2
    for index, chapter in enumerate(plan["chapters"], start=1):
        assert chapter["ordinal"] == index
        assert chapter["start_line"] <= chapter["end_line"]

    cached = compile_bundle(
        source,
        workspace,
        story_id="lantern-compiled",
        title="Compiled Lantern",
        author=NoCallCompilerAuthor(),
        parallelism=2,
        window_chapters=2,
        max_arc_chapters=2,
        world_batch_chapters=2,
    )
    assert cached.manifest_path == result.manifest_path
