from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..llm import LLMSettings
from .bundle import (
    BeatChoice,
    CanonFact,
    FactVisibility,
    PlotArc,
    Relationship,
    SourceEvidence,
    SourceManifest,
    StoryBeat,
    StoryBundle,
    StoryEntity,
    StyleProfile,
    write_bundle,
)
from .importer import SourceChapter, SourceDocument, _chapter_title, _slug

PIPELINE_VERSION = 3
SOURCE_PLAN_VERSION = 1
WORLD_SECTIONS = (
    "世界观",
    "力量体系",
    "关键人物",
    "势力描述",
    "故事主线",
    "关键物品",
    "技能体系",
)


class TextCompletionAuthor(Protocol):
    settings: LLMSettings

    async def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str: ...

    async def aclose(self) -> None: ...


class ChapterCard(BaseModel):
    """A durable, source-referenced fact card for one source chapter."""

    chapter: int
    title: str
    chapter_outline_600: str = ""
    chapter_rhythm: dict[str, Any] = Field(default_factory=dict)
    story_line: str = ""
    highlights: list[str] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    facts: list[dict[str, Any]] = Field(default_factory=list)
    source_ref: str = ""
    source_sha256: str = ""
    content_sha256: str = ""


class StoryArcRecord(BaseModel):
    arc_id: str
    start_chapter: int
    end_chapter: int
    title: str
    narrative_function: str = ""
    boundary_reason: str = ""
    structure: str = ""
    protagonist_action: str = ""
    emotion_rhythm: str = ""
    satisfaction_point: str = ""
    character_changes: str = ""
    gains_costs: str = ""
    foreshadowing: str = ""
    source_refs: list[str] = Field(default_factory=list)


class CompilationResult(BaseModel):
    workspace: str
    bundle_path: str
    world_info_path: str
    source_id: str
    title: str
    chapter_count: int
    card_count: int
    arc_count: int
    entity_count: int
    fact_count: int
    relationship_count: int
    compiler: str
    model: str
    manifest_path: str


class SourcePlanChapter(BaseModel):
    ordinal: int
    title: str
    start_line: int
    end_line: int
    reason: str = ""


class SourceStructurePlan(BaseModel):
    """LLM-selected boundaries for a raw source document.

    Line numbers refer to the immutable UTF-8 source file. The compiler only
    uses this plan to materialize source-referenced chapters; it never edits
    the original text.
    """

    plan_version: int = SOURCE_PLAN_VERSION
    source_sha256: str
    total_lines: int
    content_start_line: int
    content_end_line: int
    excluded_ranges: list[dict[str, Any]] = Field(default_factory=list)
    chapters: list[SourcePlanChapter] = Field(min_length=1)


@dataclass
class StoryCompilationWorkspace:
    """Filesystem boundary for resumable source analysis."""

    root: Path

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def source_path(self) -> Path:
        return self.root / "source.json"

    @property
    def source_plan_path(self) -> Path:
        return self.root / "source_plan.json"

    @property
    def chapter_cards_dir(self) -> Path:
        return self.root / "chapter_cards"

    @property
    def arcs_dir(self) -> Path:
        return self.root / "story_arcs"

    @property
    def world_cards_dir(self) -> Path:
        return self.root / "world_cards"

    @property
    def world_dir(self) -> Path:
        return self.root / "world_knowledge"

    @property
    def structures_dir(self) -> Path:
        return self.root / "structures"

    @property
    def bundle_path(self) -> Path:
        return self.root / "bundle.yaml"

    @property
    def world_info_path(self) -> Path:
        return self.root / "world-info.json"

    @property
    def entities_path(self) -> Path:
        return self.root / "entities.json"

    @property
    def facts_path(self) -> Path:
        return self.root / "canon-facts.json"

    @property
    def relationships_path(self) -> Path:
        return self.root / "relationships.json"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in (
            self.chapter_cards_dir,
            self.arcs_dir,
            self.world_cards_dir,
            self.world_dir,
            self.structures_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def read_json(self, path: Path, default: Any = None) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def write_json(self, path: Path, payload: Any) -> None:
        _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def write_text(self, path: Path, content: str) -> None:
        _atomic_write(path, content.rstrip() + "\n")

    def load_manifest(self) -> dict[str, Any]:
        value = self.read_json(self.manifest_path, {})
        return value if isinstance(value, dict) else {}

    def save_manifest(self, manifest: dict[str, Any]) -> None:
        manifest["updated_at"] = _now()
        self.write_json(self.manifest_path, manifest)

    def reset_derived(self) -> None:
        for directory in (
            self.chapter_cards_dir,
            self.arcs_dir,
            self.world_cards_dir,
            self.world_dir,
            self.structures_dir,
        ):
            shutil.rmtree(directory, ignore_errors=True)
        for path in (
            self.bundle_path,
            self.world_info_path,
            self.entities_path,
            self.facts_path,
            self.relationships_path,
            self.source_plan_path,
        ):
            path.unlink(missing_ok=True)
        self.ensure()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _compact(value: Any, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _string_list(value: Any, limit: int = 300) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _compact(item, limit)
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _json_text(value: Any, limit: int = 60000) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) > limit:
        return text[:limit] + "\n（输入过长，已截断。）"
    return text


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _settings_digest(settings: dict[str, Any], *, ignore_timeout: bool) -> str:
    digest_settings = (
        {key: value for key, value in settings.items() if key != "timeout"}
        if ignore_timeout
        else settings
    )
    payload = json.dumps(
        digest_settings,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _sha256_text(payload)


def _settings_fingerprint(settings: dict[str, Any]) -> str:
    return _settings_digest(settings, ignore_timeout=True)


def _legacy_settings_fingerprint(settings: dict[str, Any]) -> str:
    return _settings_digest(settings, ignore_timeout=False)


def _text_windows(text: str, limit: int) -> list[str]:
    if limit < 2000:
        raise ValueError("chapter_chars must be at least 2000")
    text = text.strip()
    if len(text) <= limit:
        return [text]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    windows: list[str] = []
    current: list[str] = []
    current_length = 0
    for paragraph in paragraphs:
        if len(paragraph) > limit:
            if current:
                windows.append("\n\n".join(current))
                current = []
                current_length = 0
            windows.extend(
                paragraph[index : index + limit] for index in range(0, len(paragraph), limit)
            )
            continue
        projected = current_length + len(paragraph) + (2 if current else 0)
        if current and projected > limit:
            windows.append("\n\n".join(current))
            current = [paragraph]
            current_length = len(paragraph)
        else:
            current.append(paragraph)
            current_length = projected
    if current:
        windows.append("\n\n".join(current))
    return windows or [text[:limit]]


def _as_records(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [
            {"name": key, **item} if isinstance(item, dict) else {"name": key, "description": item}
            for key, item in value.items()
        ]
    if value in (None, ""):
        return []
    return [value]


def _normalise_entities(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in _as_records(value):
        if isinstance(raw, str):
            if raw.strip():
                result.append({"name": raw.strip(), "kind": "other", "description": ""})
            continue
        if not isinstance(raw, dict):
            continue
        name = str(
            raw.get("name")
            or raw.get("entity")
            or raw.get("character")
            or raw.get("人物")
            or raw.get("名称")
            or ""
        ).strip()
        if not name:
            continue
        item = dict(raw)
        item["name"] = name
        item.setdefault("kind", item.get("type") or "other")
        item.setdefault("description", item.get("summary") or "")
        result.append(item)
    return result


def _normalise_facts(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in _as_records(value):
        if isinstance(raw, str):
            if raw.strip():
                result.append({"content": raw.strip(), "visibility": "public", "known_by": []})
            continue
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or raw.get("fact") or raw.get("事实") or "").strip()
        if not content:
            continue
        item = dict(raw)
        item["content"] = content
        item.setdefault("visibility", "public")
        item.setdefault("known_by", [])
        result.append(item)
    return result


def _normalise_relationships(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in _as_records(value):
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or raw.get("from") or raw.get("主体") or "").strip()
        target = str(raw.get("target") or raw.get("to") or raw.get("客体") or "").strip()
        label = str(raw.get("label") or raw.get("relation") or raw.get("关系") or "").strip()
        if source and target and label:
            result.append(
                {
                    "source": source,
                    "target": target,
                    "label": label,
                    "description": raw.get("description", ""),
                }
            )
    return result


def _unique_named(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        name = str(record.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key not in merged:
            merged[key] = dict(record)
            continue
        current = merged[key]
        for field in ("description", "kind", "type"):
            if len(str(record.get(field) or "")) > len(str(current.get(field) or "")):
                current[field] = record[field]
    return list(merged.values())


def _heading_pattern(section: str) -> re.Pattern[str]:
    return re.compile(r"(?m)^#\s*" + re.escape(section) + r"\s*$")


def _empty_world() -> str:
    return "\n\n".join("# " + section + "\n\n无" for section in WORLD_SECTIONS)


def _has_world_sections(content: str) -> bool:
    return all(_heading_pattern(section).search(content or "") for section in WORLD_SECTIONS)


def _split_world(content: str) -> dict[str, str]:
    normalized = content.strip()
    matches = [
        (section, _heading_pattern(section).search(normalized)) for section in WORLD_SECTIONS
    ]
    result: dict[str, str] = {}
    for index, pair in enumerate(matches):
        section, match = pair
        if match is None:
            result[section] = "# " + section + "\n\n无"
            continue
        end = len(normalized)
        for next_pair in matches[index + 1 :]:
            next_match = next_pair[1]
            if next_match is not None:
                end = next_match.start()
                break
        body = normalized[match.start() : end].strip()
        result[section] = body or "# " + section + "\n\n无"
    return result


def _normalise_world_document(content: str) -> str:
    if _has_world_sections(content):
        return content.strip()
    return _empty_world() + "\n\n# 未归类资料\n\n" + content.strip()


CHAPTER_SYSTEM = (
    "你是专业长篇小说拆书编辑。只分析输入章节，不得虚构。"
    "请只返回合法 JSON 对象，不要 Markdown 或解释。"
    "事实卡必须具体、可追溯；实体只记录本章明确出现的信息。"
)
ARC_SYSTEM = (
    "你是专业小说结构编辑。根据连续章节事实卡识别自然闭合的故事情节单元。"
    "片段必须从窗口第一章连续开始，不能跳章或重叠；未闭合尾部放入 carryover_reason。"
    "只返回合法 JSON 对象。"
)
SOURCE_PLAN_SYSTEM = (
    "你是长篇小说源文档结构分析员。只判断文本结构，不总结剧情。"
    "输入可能含电子书元数据、前言、目录、正文和许可证。"
    "请识别正文边界和自然章节；不得把许可证条款、目录条目或说明文字当章节。"
    "必须只返回合法 JSON 对象，不要 Markdown 或解释。"
)


WORLD_SYSTEM = (
    "你是小说世界资料架构师。根据事实卡提炼公共世界知识，不能把不确定推断写成事实。"
    "必须输出完整七栏，并保留人物实力的阶段轨迹。"
)


def _chapter_prompt(
    document: SourceDocument,
    chapter: SourceChapter,
    text: str,
    part: int,
    total: int,
) -> list[dict[str, str]]:
    schema = {
        "chapter": chapter.ordinal,
        "title": "章节名",
        "chapter_outline_600": "具体写清起因、行动、冲突、信息和章末钩子",
        "chapter_rhythm": {
            "core_content": "短语链",
            "emotion_tone": "情绪链",
            "beat_detail": "节奏细节",
        },
        "story_line": "主角行动链，用 + 连接",
        "highlights": ["亮点或爆点"],
        "entities": [
            {
                "name": "名称",
                "kind": "character|place|organization|object|skill|other",
                "description": "明确事实",
            }
        ],
        "relationships": [
            {"source": "实体", "target": "实体", "label": "关系", "description": "证据"}
        ],
        "facts": [
            {
                "content": "事实",
                "visibility": "public|player|character|author_only",
                "known_by": ["实体"],
            }
        ],
    }
    user = (
        "来源："
        + document.title
        + "\n语言："
        + document.locale
        + "\n全书第 "
        + str(chapter.ordinal)
        + " 章："
        + chapter.title
        + "\n正文窗口："
        + str(part)
        + "/"
        + str(total)
        + "\n\n只根据以下正文分析：\n---\n"
        + text
        + "\n---\n\n请按以下 JSON 结构返回：\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
        + "\nchapter 必须使用真实章节序号；不要编造人物、势力、能力或情节。"
    )
    return [{"role": "system", "content": CHAPTER_SYSTEM}, {"role": "user", "content": user}]


def _arc_prompt(
    cards: Sequence[ChapterCard],
    previous_tail: str,
    is_final: bool,
    max_arc_chapters: int,
) -> list[dict[str, str]]:
    start = cards[0].chapter
    end = cards[-1].chapter
    schema = {
        "completed_segments": [
            {
                "title": "片段标题",
                "start_chapter": start,
                "end_chapter": end,
                "narrative_function": "情节功能",
                "boundary_reason": "自然边界原因",
                "structure": "触发 -> 加压 -> 转折 -> 收束或新困境",
                "protagonist_action": "主角行动链",
                "emotion_rhythm": "情绪链",
                "satisfaction_point": "爽点或张力",
                "character_changes": "人物变化",
                "gains_costs": "收获与代价",
                "foreshadowing": "伏笔",
            }
        ],
        "carryover_reason": "未闭合尾部原因；没有则写无",
    }
    tail = previous_tail or "无"
    final_label = "是" if is_final else "否"
    user = (
        "窗口范围：第 "
        + str(start)
        + "-"
        + str(end)
        + " 章\n"
        + "单个片段最大章节数："
        + str(max_arc_chapters)
        + "\n"
        + "最终窗口："
        + final_label
        + "\n"
        + "上一次未闭合尾部："
        + tail
        + "\n\n"
        + "事实卡：\n"
        + _json_text([card.model_dump(mode="json") for card in cards])
        + "\n\ncompleted_segments 必须从窗口第一章开始连续输出。JSON 结构示例：\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
    )
    return [{"role": "system", "content": ARC_SYSTEM}, {"role": "user", "content": user}]


def _world_prompt(cards: Sequence[ChapterCard], label: str) -> list[dict[str, str]]:
    headings = "\n".join("# " + section for section in WORLD_SECTIONS)
    user = (
        "资料窗口："
        + label
        + "\n以下是章节事实卡：\n"
        + _json_text([card.model_dump(mode="json") for card in cards])
        + "\n\n输出完整七栏；无内容写‘无’；补充设定标注‘推断/候选公共补充’。\n"
        + headings
    )
    return [{"role": "system", "content": WORLD_SYSTEM}, {"role": "user", "content": user}]


def _world_merge_prompt(previous: str, current: str, label: str) -> list[dict[str, str]]:
    headings = "\n".join("# " + section for section in WORLD_SECTIONS)
    user = (
        "合并位置："
        + label
        + "\n已有阶段知识库：\n"
        + previous
        + "\n\n本轮新增资料：\n"
        + current
        + "\n\n请输出合并后的完整七栏，不要只输出新增内容。"
        "故事主线最严格；力量体系、关键物品、技能体系可在明确属于公共世界时补全。\n" + headings
    )
    return [{"role": "system", "content": WORLD_SYSTEM}, {"role": "user", "content": user}]


def _structure_prompt(
    title: str,
    start: int,
    end: int,
    arcs: Sequence[StoryArcRecord],
) -> list[dict[str, str]]:
    user = (
        "请将《"
        + title
        + "》第 "
        + str(start)
        + "-"
        + str(end)
        + " 章的自然情节单元合并成可用于 TARI Story Mode 的结构摘要。\n"
        + _json_text([arc.model_dump(mode="json") for arc in arcs])
        + "\n\n输出纯文本，包含：\n# 概览\n# 核心矛盾与主角目标\n"
        + "# 三阶段推进\n# 人物与关系变化\n# 伏笔与信息差\n# 高潮与收获\n"
        + "只能总结给定事实。"
    )
    return [
        {"role": "system", "content": "你是长篇小说结构整理员，只能依据提供的情节单元。"},
        {"role": "user", "content": user},
    ]


def _novel_prompt(title: str, outlines: Sequence[str]) -> list[dict[str, str]]:
    user = (
        "请汇总《"
        + title
        + "》的全书结构，供 TARI 互动叙事理解主线。\n"
        + "\n\n---\n\n".join(outlines)
        + "\n\n输出纯文本并包含：\n# 定位与核心设定\n# 全书主线\n"
        + "# 人物成长与关系\n# 伏笔与信息差\n# 力量与资源成长\n# 各阶段核心冲突\n"
        + "只总结已经提供的结构。"
    )
    return [
        {"role": "system", "content": "你是完整小说大纲整理员，只能依据已闭合的情节结构。"},
        {"role": "user", "content": user},
    ]


async def _complete_text(
    author: TextCompletionAuthor,
    messages: list[dict[str, str]],
    label: str,
    max_tokens: int,
) -> str:
    last_error: Exception | None = None
    last_raw = ""
    for attempt in range(3):
        current = list(messages)
        if attempt:
            current.append(
                {
                    "role": "user",
                    "content": "上次任务失败：" + str(last_error) + "。请重新输出完整结果。",
                }
            )
        try:
            raw = await author.complete_text(current, temperature=0.2, max_tokens=max_tokens)
            last_raw = str(raw or "")
            text = last_raw.strip()
            if text:
                return text
            raise ValueError("模型输出为空")
        except Exception as exc:  # noqa: BLE001 - retry is part of the pipeline contract
            last_error = exc
    raise RuntimeError(
        label + "生成失败：" + str(last_error or "未知错误") + " | raw=" + repr(last_raw[:300])
    )


async def _complete_json(
    author: TextCompletionAuthor,
    messages: list[dict[str, str]],
    label: str,
    max_tokens: int,
) -> dict[str, Any]:
    from ..llm import extract_json_object

    last_error: Exception | None = None
    last_raw: str = ""
    for attempt in range(3):
        current = list(messages)
        if attempt:
            current.append(
                {
                    "role": "user",
                    "content": "上次输出无法解析为 JSON："
                    + str(last_error)
                    + "。只返回合法 JSON 对象。",
                }
            )
        try:
            complete_json = getattr(author, "complete_json", None)
            if callable(complete_json):
                payload = await complete_json(
                    current,
                    temperature=0.2,
                    max_tokens=max_tokens,
                )
            else:
                raw_text = await author.complete_text(
                    current,
                    temperature=0.2,
                    max_tokens=max_tokens,
                )
                last_raw = raw_text
                payload = extract_json_object(raw_text)
            if isinstance(payload, dict):
                return payload
            raise ValueError("模型输出不是 JSON 对象")
        except Exception as exc:  # noqa: BLE001 - retry is part of the pipeline contract
            last_error = exc
    raise RuntimeError(
        label + " JSON 解析失败：" + str(last_error) + " | raw=" + repr((last_raw or "")[:200])
    )


def _source_structure_candidates(lines: Sequence[str], limit: int = 320) -> list[dict[str, Any]]:
    """Build a bounded, line-numbered structural index for the source planner.

    This is only a prompt index. It does not decide chapter boundaries; the
    LLM receives the candidates and returns the authoritative source plan.
    """
    candidates: list[dict[str, Any]] = []
    for number, raw_line in enumerate(lines, start=1):
        text = re.sub(r"\s+", " ", raw_line.strip())
        if not text:
            continue
        strong = bool(
            re.match(
                r"^(?:book|chapter|part|volume|canto|act|scene)\b",
                text,
                re.IGNORECASE,
            )
            or re.match(r"^(?:第|序章|楔子|尾声|终章)", text)
            or re.match(r"^#{1,6}\s+", text)
        )
        numbered = bool(re.match(r"^\d+(?:\.[A-Za-z])?(?:[.)、:：])\s*\S", text))
        all_caps = (
            len(text) <= 120 and sum(char.isalpha() for char in text) >= 4 and text.upper() == text
        )
        if not (strong or numbered or all_caps):
            continue
        kind = "chapter_candidate" if strong else "numbered_or_heading"
        candidates.append(
            {
                "line": number,
                "kind": kind,
                "text": text[:240],
                "context_before": re.sub(r"\s+", " ", lines[number - 2].strip())[:120]
                if number > 1
                else "",
                "context_after": re.sub(r"\s+", " ", lines[number].strip())[:120]
                if number < len(lines)
                else "",
                "hint": (
                    "possible chapter boundary"
                    if strong
                    else "must classify, may be metadata/license"
                ),
            }
        )

    if len(candidates) <= limit:
        return candidates
    priority = [item for item in candidates if item["kind"] == "chapter_candidate"]
    remainder = [item for item in candidates if item["kind"] != "chapter_candidate"]
    budget = max(0, limit - len(priority))
    return sorted([*priority, *remainder[:budget]], key=lambda item: int(item["line"]))


def _source_plan_prompt(
    title: str,
    source_sha256: str,
    lines: Sequence[str],
    candidates: Sequence[dict[str, Any]],
) -> list[dict[str, str]]:
    schema = {
        "source_sha256": source_sha256,
        "total_lines": len(lines),
        "content_start_line": 1,
        "content_end_line": len(lines),
        "excluded_ranges": [{"start_line": 1, "end_line": 10, "reason": "metadata or license"}],
        "chapters": [
            {
                "ordinal": 1,
                "title": "Book I",
                "start_line": 100,
                "end_line": 300,
                "reason": "real literary chapter, not a license section",
            }
        ],
    }
    index = {
        "first_lines": [
            {"line": number, "text": re.sub(r"\s+", " ", lines[number - 1].strip())[:180]}
            for number in range(1, min(41, len(lines) + 1))
        ],
        "last_lines": [
            {"line": number, "text": re.sub(r"\s+", " ", lines[number - 1].strip())[:180]}
            for number in range(max(1, len(lines) - 39), len(lines) + 1)
        ],
        "candidates": list(candidates),
    }
    user = (
        "书名："
        + title
        + "\n源文件 SHA-256（必须原样回传）："
        + source_sha256
        + "\n总行数（行号从 1 开始，区间两端均包含）："
        + str(len(lines))
        + "\n\n这是原始电子书的结构索引，不是剧情正文。请只根据索引识别结构。"
        + "不要把 Project Gutenberg/版权/许可证/目录/元数据/编辑说明识别成故事章节。"
        + "如果正文有 Book/Canto/Chapter 等自然章节，必须逐章列出；"
        + "每章 start_line 必须指向章节标题，"
        + "end_line 指向该章正文结束处。不要修改原始文本。"
        + "\n\n结构索引：\n"
        + _json_text(index, 58000)
        + "\n\n只返回符合以下 JSON 形状的对象：\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": SOURCE_PLAN_SYSTEM},
        {"role": "user", "content": user},
    ]


def _normalise_source_plan(
    payload: dict[str, Any],
    source_sha256: str,
    total_lines: int,
) -> SourceStructurePlan:
    payload_sha = payload.get("source_sha256")
    if payload_sha and payload_sha != source_sha256:
        raise ValueError("source plan SHA-256 does not match the source file")
    if payload.get("total_lines") and int(payload["total_lines"]) != total_lines:
        raise ValueError("source plan total_lines does not match the source file")
    raw_chapters = payload.get("chapters") or payload.get("sections") or []
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise ValueError("source plan contains no chapters")
    chapters: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_chapters, start=1):
        if not isinstance(raw, dict):
            raise ValueError("source plan chapter is not an object")
        start = raw.get("start_line", raw.get("start"))
        end = raw.get("end_line", raw.get("end"))
        if start is None or end is None:
            raise ValueError("source plan chapter is missing line bounds")
        try:
            start_line = int(start)
            end_line = int(end)
        except (TypeError, ValueError) as exc:
            raise ValueError("source plan chapter line bounds are not integers") from exc
        if start_line < 1 or end_line < start_line or end_line > total_lines:
            raise ValueError("source plan chapter line bounds are outside the source")
        chapters.append(
            {
                "ordinal": index,
                "title": _compact(
                    raw.get("title") or raw.get("name") or ("Chapter " + str(index)),
                    240,
                ),
                "start_line": start_line,
                "end_line": end_line,
                "reason": _compact(raw.get("reason") or raw.get("boundary_reason"), 800),
            }
        )
    for previous, current in zip(chapters, chapters[1:], strict=False):
        if current["start_line"] <= previous["end_line"]:
            raise ValueError("source plan chapters overlap or are not ordered")

    first_start = chapters[0]["start_line"]
    last_end = chapters[-1]["end_line"]
    try:
        content_start = int(payload.get("content_start_line") or first_start)
        content_end = int(payload.get("content_end_line") or last_end)
    except (TypeError, ValueError) as exc:
        raise ValueError("source plan content bounds are not integers") from exc
    if not 1 <= content_start <= content_end <= total_lines:
        raise ValueError("source plan content bounds are outside the source")
    if first_start < content_start or last_end > content_end:
        raise ValueError("source plan chapters fall outside content bounds")

    excluded = payload.get("excluded_ranges") or []
    if not isinstance(excluded, list):
        raise ValueError("source plan excluded_ranges must be a list")
    return SourceStructurePlan.model_validate(
        {
            "plan_version": SOURCE_PLAN_VERSION,
            "source_sha256": source_sha256,
            "total_lines": total_lines,
            "content_start_line": content_start,
            "content_end_line": content_end,
            "excluded_ranges": excluded,
            "chapters": chapters,
        }
    )


def _materialise_source_document(
    source_path: Path,
    raw: bytes,
    source_id: str,
    title: str | None,
    locale: str,
    plan: SourceStructurePlan,
) -> SourceDocument:
    text = raw.decode("utf-8")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(lines) != plan.total_lines:
        raise ValueError("source changed while applying the source plan")
    chapters: list[SourceChapter] = []
    normalized_source_id = _slug(source_id)
    for item in plan.chapters:
        chapter_text = "\n".join(lines[item.start_line - 1 : item.end_line]).strip()
        if not chapter_text:
            raise ValueError("source plan selected an empty chapter: " + str(item.ordinal))
        ordinal = str(item.ordinal).zfill(3)
        chapters.append(
            SourceChapter(
                chapter_id=normalized_source_id + "-chapter-" + ordinal,
                ordinal=item.ordinal,
                title=_chapter_title(item.title, item.ordinal),
                text=chapter_text,
                source_ref=normalized_source_id + ":chapter:" + ordinal,
                sha256=_sha256_text(chapter_text),
            )
        )
    return SourceDocument(
        source_id=normalized_source_id,
        title=title or source_path.stem.replace("_", " ").replace("-", " ").strip(),
        kind="markdown" if source_path.suffix.lower() in {".md", ".markdown"} else "text",
        locale=locale,
        encoding="utf-8",
        source_path=str(source_path),
        sha256=hashlib.sha256(raw).hexdigest(),
        chapters=chapters,
    )


async def _prepare_source_document(
    source_path: Path,
    workspace: StoryCompilationWorkspace,
    *,
    author: TextCompletionAuthor,
    source_id: str,
    title: str | None,
    locale: str,
    rebuild: bool,
) -> tuple[SourceDocument, SourceStructurePlan]:
    if source_path.suffix.lower() not in {".txt", ".md", ".markdown"}:
        raise ValueError("source must be a .txt, .md, or .markdown file")
    raw = source_path.read_bytes()
    raw.decode("utf-8")
    text = raw.decode("utf-8")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    source_sha256 = hashlib.sha256(raw).hexdigest()
    cached = workspace.read_json(workspace.source_plan_path)
    plan: SourceStructurePlan | None = None
    if not rebuild and isinstance(cached, dict):
        try:
            candidate = SourceStructurePlan.model_validate(cached)
            if (
                candidate.source_sha256 == source_sha256
                and candidate.total_lines == len(lines)
                and candidate.plan_version == SOURCE_PLAN_VERSION
            ):
                plan = candidate
        except Exception:
            plan = None
        cached_sha = cached.get("source_sha256") if isinstance(cached, dict) else None
        if plan is None and cached_sha and cached_sha != source_sha256:
            raise ValueError("source changed since the last compilation; use rebuild=True")
    if plan is None and not rebuild:
        manifest = workspace.load_manifest()
        old_hash = manifest.get("source_sha256")
        if old_hash and old_hash != source_sha256:
            raise ValueError("source changed since the last compilation; use rebuild=True")

    if plan is None:
        candidates = _source_structure_candidates(lines)
        try:
            payload = await _complete_json(
                author,
                _source_plan_prompt(
                    title or source_path.stem,
                    source_sha256,
                    lines,
                    candidates,
                ),
                "源文档结构规划",
                6000,
            )
            plan = _normalise_source_plan(payload, source_sha256, len(lines))
            workspace.write_json(workspace.source_plan_path, plan.model_dump(mode="json"))
        except Exception as exc:
            manifest = workspace.load_manifest()
            manifest.update(
                {
                    "pipeline_version": PIPELINE_VERSION,
                    "source_id": _slug(source_id),
                    "source_path": str(source_path),
                    "source_sha256": source_sha256,
                    "total_lines": len(lines),
                    "status": "failed",
                }
            )
            manifest.setdefault("failures", {})["source_plan"] = {
                "error": str(exc),
                "at": _now(),
            }
            workspace.save_manifest(manifest)
            raise

    document = _materialise_source_document(source_path, raw, source_id, title, locale, plan)
    manifest = workspace.load_manifest()
    if rebuild:
        manifest = {}
    manifest.update(
        {
            "pipeline_version": PIPELINE_VERSION,
            "source_id": document.source_id,
            "source_path": document.source_path,
            "source_sha256": document.sha256,
            "total_lines": plan.total_lines,
            "planned_chapters": len(plan.chapters),
            "status": "in_progress",
        }
    )
    manifest.setdefault("stages", {})["source_plan"] = True
    manifest.setdefault("stages", {})["source_plan_version"] = SOURCE_PLAN_VERSION
    workspace.save_manifest(manifest)
    return document, plan


def _normalise_card(
    payload: dict[str, Any],
    chapter: SourceChapter,
    source_sha256: str,
) -> ChapterCard:
    rhythm = payload.get("chapter_rhythm") or {}
    if not isinstance(rhythm, dict):
        rhythm = {"core_content": rhythm}
    return ChapterCard(
        chapter=chapter.ordinal,
        title=_compact(payload.get("title") or chapter.title, 200),
        chapter_outline_600=_compact(
            payload.get("chapter_outline_600") or payload.get("summary") or payload.get("outline"),
            3000,
        ),
        chapter_rhythm={key: _compact(value, 800) for key, value in rhythm.items()},
        story_line=_compact(payload.get("story_line") or payload.get("plot_line"), 1000),
        highlights=_string_list(payload.get("highlights") or payload.get("亮点")),
        entities=_normalise_entities(
            payload.get("entities") or payload.get("characters") or payload.get("人物")
        ),
        relationships=_normalise_relationships(
            payload.get("relationships") or payload.get("relations")
        ),
        facts=_normalise_facts(
            payload.get("facts") or payload.get("canon_facts") or payload.get("世界事实")
        ),
        source_ref=chapter.source_ref,
        source_sha256=source_sha256,
        content_sha256=chapter.sha256,
    )


def _merge_card_parts(
    parts: Sequence[ChapterCard],
    chapter: SourceChapter,
    source_sha256: str,
) -> ChapterCard:
    if len(parts) == 1:
        return parts[0]
    facts: dict[str, dict[str, Any]] = {}
    relationships: dict[tuple[str, str, str], dict[str, Any]] = {}
    for part in parts:
        for fact in part.facts:
            facts.setdefault(str(fact.get("content") or "").casefold(), fact)
        for relation in part.relationships:
            key = (
                relation["source"].casefold(),
                relation["target"].casefold(),
                relation["label"].casefold(),
            )
            relationships.setdefault(key, relation)
    return ChapterCard(
        chapter=chapter.ordinal,
        title=chapter.title,
        chapter_outline_600="\n\n".join(
            part.chapter_outline_600 for part in parts if part.chapter_outline_600
        ),
        chapter_rhythm={
            key: "；".join(
                str(part.chapter_rhythm.get(key, ""))
                for part in parts
                if part.chapter_rhythm.get(key)
            )
            for key in {key for part in parts for key in part.chapter_rhythm}
        },
        story_line=" + ".join(part.story_line for part in parts if part.story_line),
        highlights=_string_list([item for part in parts for item in part.highlights]),
        entities=_unique_named([entity for part in parts for entity in part.entities]),
        relationships=list(relationships.values()),
        facts=list(facts.values()),
        source_ref=chapter.source_ref,
        source_sha256=source_sha256,
        content_sha256=chapter.sha256,
    )


async def _extract_one_card(
    document: SourceDocument,
    chapter: SourceChapter,
    author: TextCompletionAuthor,
    chapter_chars: int,
) -> ChapterCard:
    windows = _text_windows(chapter.text, chapter_chars)
    parts: list[ChapterCard] = []
    for index, text in enumerate(windows, start=1):
        payload = await _complete_json(
            author,
            _chapter_prompt(document, chapter, text, index, len(windows)),
            "第" + str(chapter.ordinal) + "章事实卡",
            3500,
        )
        parts.append(_normalise_card(payload, chapter, document.sha256))
    return _merge_card_parts(parts, chapter, document.sha256)


def _cached_card(
    workspace: StoryCompilationWorkspace,
    chapter: SourceChapter,
    source_sha256: str,
) -> ChapterCard | None:
    path = workspace.chapter_cards_dir / ("chapter_" + str(chapter.ordinal).zfill(4) + ".json")
    payload = workspace.read_json(path)
    if not isinstance(payload, dict):
        return None
    if payload.get("source_sha256") != source_sha256:
        return None
    if payload.get("content_sha256") != chapter.sha256:
        return None
    try:
        return ChapterCard.model_validate(payload)
    except Exception:
        return None


async def _extract_cards(
    document: SourceDocument,
    workspace: StoryCompilationWorkspace,
    author: TextCompletionAuthor,
    target: int,
    parallelism: int,
    chapter_chars: int,
    manifest: dict[str, Any],
) -> list[ChapterCard]:
    chapters = list(document.chapters[:target])
    cards: dict[int, ChapterCard] = {}
    pending: list[SourceChapter] = []
    for chapter in chapters:
        cached = _cached_card(workspace, chapter, document.sha256)
        if cached is None:
            pending.append(chapter)
        else:
            cards[chapter.ordinal] = cached

    semaphore = asyncio.Semaphore(max(1, parallelism))

    async def run(chapter: SourceChapter) -> ChapterCard:
        async with semaphore:
            return await _extract_one_card(document, chapter, author, chapter_chars)

    results = await asyncio.gather(*(run(chapter) for chapter in pending), return_exceptions=True)
    errors: list[str] = []
    for chapter, result in zip(pending, results, strict=True):
        if isinstance(result, BaseException):
            errors.append("第" + str(chapter.ordinal) + "章：" + str(result))
            continue
        cards[chapter.ordinal] = result
        path = workspace.chapter_cards_dir / ("chapter_" + str(chapter.ordinal).zfill(4) + ".json")
        workspace.write_json(path, result.model_dump(mode="json"))
        manifest.setdefault("stages", {})["chapter_cards_completed"] = len(cards)
        workspace.save_manifest(manifest)
    if errors:
        raise RuntimeError("章节事实卡存在失败，可直接重试续跑：\n" + "\n".join(errors[:8]))
    return [cards[index] for index in sorted(cards)]


def _normalise_segments(
    payload: dict[str, Any],
    cards: Sequence[ChapterCard],
    max_arc_chapters: int,
) -> list[dict[str, Any]]:
    candidates = payload.get("completed_segments") or payload.get("segments") or []
    if not isinstance(candidates, list):
        return []
    available = {card.chapter for card in cards}
    expected = cards[0].chapter
    accepted: list[dict[str, Any]] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            break
        raw_start = raw.get("start_chapter")
        raw_end = raw.get("end_chapter")
        if raw_start is None or raw_end is None:
            break
        try:
            start = int(raw_start)
            end = int(raw_end)
        except (TypeError, ValueError):
            break
        if start != expected or end < start:
            break
        if end - start + 1 > max_arc_chapters:
            break
        if any(number not in available for number in range(start, end + 1)):
            break
        accepted.append(
            {
                "start_chapter": start,
                "end_chapter": end,
                "title": _compact(
                    raw.get("title") or ("第" + str(start) + "-" + str(end) + "章情节"), 200
                ),
                "narrative_function": _compact(raw.get("narrative_function"), 1200),
                "boundary_reason": _compact(raw.get("boundary_reason"), 1200),
                "structure": _compact(raw.get("structure"), 1800),
                "protagonist_action": _compact(raw.get("protagonist_action"), 1600),
                "emotion_rhythm": _compact(raw.get("emotion_rhythm"), 1200),
                "satisfaction_point": _compact(raw.get("satisfaction_point"), 1200),
                "character_changes": _compact(raw.get("character_changes"), 1400),
                "gains_costs": _compact(raw.get("gains_costs"), 1200),
                "foreshadowing": _compact(raw.get("foreshadowing"), 1400),
            }
        )
        expected = end + 1
    return accepted


def _arc_from_raw(
    raw: dict[str, Any],
    index: int,
    cards_by_chapter: dict[int, ChapterCard],
) -> StoryArcRecord:
    start = int(raw["start_chapter"])
    end = int(raw["end_chapter"])
    refs = [
        cards_by_chapter[number].source_ref
        for number in range(start, end + 1)
        if number in cards_by_chapter
    ]
    return StoryArcRecord(
        arc_id="arc-" + str(index).zfill(4),
        start_chapter=start,
        end_chapter=end,
        title=str(raw.get("title") or ("第" + str(start) + "-" + str(end) + "章情节")),
        narrative_function=str(raw.get("narrative_function") or ""),
        boundary_reason=str(raw.get("boundary_reason") or ""),
        structure=str(raw.get("structure") or ""),
        protagonist_action=str(raw.get("protagonist_action") or ""),
        emotion_rhythm=str(raw.get("emotion_rhythm") or ""),
        satisfaction_point=str(raw.get("satisfaction_point") or ""),
        character_changes=str(raw.get("character_changes") or ""),
        gains_costs=str(raw.get("gains_costs") or ""),
        foreshadowing=str(raw.get("foreshadowing") or ""),
        source_refs=refs,
    )


def _fallback_arc(cards: Sequence[ChapterCard], index: int) -> StoryArcRecord:
    start = cards[0].chapter
    end = cards[-1].chapter
    return StoryArcRecord(
        arc_id="arc-" + str(index).zfill(4),
        start_chapter=start,
        end_chapter=end,
        title="第" + str(start) + "-" + str(end) + "章过渡情节",
        narrative_function="基于章节事实卡的阶段推进。",
        boundary_reason="模型未提供可靠边界，保留当前窗口供后续复核。",
        structure="；".join(card.story_line for card in cards if card.story_line),
        protagonist_action="；".join(card.chapter_outline_600 for card in cards),
        emotion_rhythm="；".join(
            str(card.chapter_rhythm.get("emotion_tone") or "") for card in cards
        ),
        satisfaction_point="待复核",
        character_changes="；".join(card.story_line for card in cards if card.story_line),
        gains_costs="待复核",
        foreshadowing="；".join("；".join(card.highlights) for card in cards),
        source_refs=[card.source_ref for card in cards],
    )


def _render_arc(arc: StoryArcRecord) -> str:
    lines = [
        "【" + arc.title + "：第" + str(arc.start_chapter) + "-" + str(arc.end_chapter) + "章】",
        "情节功能：" + arc.narrative_function,
        "自然边界判断：" + arc.boundary_reason,
        "起承转合：" + arc.structure,
        "主角行动链：" + arc.protagonist_action,
        "矛盾与情绪：" + arc.emotion_rhythm,
        "核心爽点或张力：" + arc.satisfaction_point,
        "角色与关系变化：" + arc.character_changes,
        "收获与代价：" + arc.gains_costs,
        "伏笔与后续困境：" + arc.foreshadowing,
    ]
    return "\n\n".join(lines)


async def _extract_arcs(
    workspace: StoryCompilationWorkspace,
    cards: Sequence[ChapterCard],
    author: TextCompletionAuthor,
    window_chapters: int,
    max_arc_chapters: int,
    manifest: dict[str, Any],
) -> list[StoryArcRecord]:
    if window_chapters < 2:
        raise ValueError("window_chapters must be at least 2")
    source_sha256 = str(manifest["source_sha256"])
    state_path = workspace.arcs_dir / "arcs_state.json"
    state = workspace.read_json(state_path, {})
    if not isinstance(state, dict) or state.get("source_sha256") != source_sha256:
        state = {"source_sha256": source_sha256, "cursor": 0, "carryover": [], "records": []}
    records = [StoryArcRecord.model_validate(item) for item in state.get("records", [])]
    cards_by_number = {card.chapter: card for card in cards}
    cursor = min(int(state.get("cursor") or 0), len(cards))
    carryover = [int(number) for number in state.get("carryover") or []]
    if records and records[-1].end_chapter > cards[-1].chapter:
        records = []
        cursor = 0
        carryover = []
    next_index = len(records) + 1

    while cursor < len(cards) or carryover:
        new_cards = list(cards[cursor : cursor + window_chapters])
        cursor += len(new_cards)
        window = [cards_by_number[number] for number in carryover if number in cards_by_number]
        known = {card.chapter for card in window}
        window.extend(card for card in new_cards if card.chapter not in known)
        window.sort(key=lambda card: card.chapter)
        if not window:
            break
        is_final = cursor >= len(cards)
        payload = await _complete_json(
            author,
            _arc_prompt(
                window, ", ".join(str(number) for number in carryover), is_final, max_arc_chapters
            ),
            "第" + str(window[0].chapter) + "-" + str(window[-1].chapter) + "章故事片段",
            4000,
        )
        raw_segments = _normalise_segments(payload, window, max_arc_chapters)
        if not raw_segments:
            if is_final:
                consume_end = window[-1].chapter
            else:
                consume_end = min(window[-1].chapter - 1, window[0].chapter + max_arc_chapters - 1)
            fallback_cards = [card for card in window if card.chapter <= consume_end]
            if not fallback_cards:
                fallback_cards = [window[0]]
                consume_end = window[0].chapter
            arc = _fallback_arc(fallback_cards, next_index)
            records.append(arc)
            raw_segments = [{"start_chapter": arc.start_chapter, "end_chapter": arc.end_chapter}]
            arc_path = workspace.arcs_dir / ("arc_" + str(next_index).zfill(4))
            workspace.write_json(Path(str(arc_path) + ".json"), arc.model_dump(mode="json"))
            workspace.write_text(Path(str(arc_path) + ".md"), _render_arc(arc))
            next_index += 1
        else:
            for raw in raw_segments:
                arc = _arc_from_raw(raw, next_index, cards_by_number)
                records.append(arc)
                arc_path = workspace.arcs_dir / ("arc_" + str(next_index).zfill(4))
                workspace.write_json(Path(str(arc_path) + ".json"), arc.model_dump(mode="json"))
                workspace.write_text(Path(str(arc_path) + ".md"), _render_arc(arc))
                next_index += 1
        consumed = int(raw_segments[-1]["end_chapter"])
        carryover = [card.chapter for card in window if card.chapter > consumed]
        state = {
            "source_sha256": source_sha256,
            "cursor": cursor,
            "carryover": carryover,
            "records": [record.model_dump(mode="json") for record in records],
        }
        workspace.write_json(state_path, state)
        workspace.write_json(
            workspace.arcs_dir / "arcs_index.json",
            [record.model_dump(mode="json") for record in records],
        )
        manifest.setdefault("stages", {})["story_arcs_completed_through"] = records[-1].end_chapter
        manifest.setdefault("stages", {})["story_arcs"] = True
        workspace.save_manifest(manifest)
    return records


async def _extract_world(
    workspace: StoryCompilationWorkspace,
    cards: Sequence[ChapterCard],
    author: TextCompletionAuthor,
    batch_chapters: int,
    parallelism: int,
    manifest: dict[str, Any],
) -> str:
    if batch_chapters < 1:
        raise ValueError("world_batch_chapters must be greater than zero")
    source_sha256 = str(manifest["source_sha256"])
    batches = [
        list(cards[index : index + batch_chapters])
        for index in range(0, len(cards), batch_chapters)
    ]
    # The local llama-server has one generation slot. Process batches in order
    # even when the caller requests higher parallelism; this avoids competing
    # long requests and lets each successful batch become a durable checkpoint.
    world_cards: dict[int, str] = {}
    for index, batch in enumerate(batches, start=1):
        path = workspace.world_cards_dir / ("world_card_" + str(index).zfill(4) + ".json")
        cached = workspace.read_json(path)
        if (
            isinstance(cached, dict)
            and cached.get("source_sha256") == source_sha256
            and cached.get("start") == batch[0].chapter
            and cached.get("end") == batch[-1].chapter
            and _has_world_sections(str(cached.get("content") or ""))
        ):
            world_cards[index] = str(cached["content"])
            manifest.setdefault("stages", {})["world_cards_completed"] = index
            workspace.save_manifest(manifest)
            continue
        try:
            content = await _complete_text(
                author,
                _world_prompt(
                    batch, "第" + str(batch[0].chapter) + "-" + str(batch[-1].chapter) + "章"
                ),
                "世界知识卡 " + str(index),
                5000,
            )
            content = _normalise_world_document(content)
            workspace.write_json(
                path,
                {
                    "source_sha256": source_sha256,
                    "start": batch[0].chapter,
                    "end": batch[-1].chapter,
                    "content": content,
                },
            )
            workspace.write_text(
                workspace.world_cards_dir / ("world_card_" + str(index).zfill(4) + ".md"),
                content,
            )
            world_cards[index] = content
            manifest.setdefault("stages", {})["world_cards_completed"] = index
            manifest.setdefault("failures", {}).pop("world_cards", None)
            workspace.save_manifest(manifest)
        except Exception as exc:  # noqa: BLE001 - preserve batch checkpoint and retry later
            manifest.setdefault("failures", {})["world_cards"] = {
                "batch": index,
                "start_chapter": batch[0].chapter,
                "end_chapter": batch[-1].chapter,
                "error": str(exc),
                "at": _now(),
            }
            manifest["status"] = "failed"
            workspace.save_manifest(manifest)
            raise RuntimeError(
                "世界知识卡 " + str(index) + "生成失败，可直接重试续跑：" + str(exc)
            ) from exc

    previous = _empty_world()
    for index in sorted(world_cards):
        path = workspace.world_cards_dir / ("world_merge_" + str(index).zfill(4) + ".json")
        input_digest = _sha256_text(previous + "\n" + world_cards[index])
        cached = workspace.read_json(path)
        if (
            isinstance(cached, dict)
            and cached.get("source_sha256") == source_sha256
            and cached.get("card_index") == index
            and cached.get("input_digest") == input_digest
            and _has_world_sections(str(cached.get("content") or ""))
        ):
            previous = str(cached["content"])
            manifest.setdefault("stages", {})["world_merge_completed"] = index
            workspace.save_manifest(manifest)
            continue
        merge_messages = _world_merge_prompt(
            previous,
            world_cards[index],
            "资料卡 " + str(index) + "/" + str(len(world_cards)),
        )
        try:
            previous = await _complete_text(
                author,
                merge_messages,
                "世界知识合并 " + str(index),
                4500,
            )
            previous = _normalise_world_document(previous)
        except Exception as exc:  # noqa: BLE001 - persist the exact resumable boundary
            manifest.setdefault("failures", {})["world_merge"] = {
                "card_index": index,
                "input_digest": input_digest,
                "prompt_chars": sum(len(message["content"]) for message in merge_messages),
                "error": str(exc),
                "at": _now(),
            }
            manifest["status"] = "failed"
            workspace.save_manifest(manifest)
            raise RuntimeError(
                "世界知识合并 " + str(index) + "生成失败，可直接重试续跑：" + str(exc)
            ) from exc
        workspace.write_json(
            path,
            {
                "source_sha256": source_sha256,
                "card_index": index,
                "input_digest": input_digest,
                "content": previous,
            },
        )
        workspace.write_text(
            workspace.world_cards_dir / ("world_merge_" + str(index).zfill(4) + ".md"),
            previous,
        )
        manifest.setdefault("stages", {})["world_merge_completed"] = index
        manifest.setdefault("failures", {}).pop("world_merge", None)
        workspace.save_manifest(manifest)

    workspace.write_text(workspace.world_dir / "world_knowledge.md", previous)
    for index, pair in enumerate(_split_world(previous).items(), start=1):
        section, content = pair
        filename = "section_" + str(index).zfill(2) + "_" + _slug(section) + ".md"
        workspace.write_text(workspace.world_dir / filename, content)
    return previous


async def _build_structures(
    workspace: StoryCompilationWorkspace,
    document: SourceDocument,
    arcs: Sequence[StoryArcRecord],
    author: TextCompletionAuthor,
    volume_size: int,
    manifest: dict[str, Any],
) -> tuple[list[str], str]:
    if volume_size < 1:
        raise ValueError("volume_size must be greater than zero")
    source_sha256 = str(manifest["source_sha256"])
    groups: dict[int, list[StoryArcRecord]] = {}
    for arc in arcs:
        bucket = (arc.start_chapter - 1) // volume_size
        groups.setdefault(bucket, []).append(arc)
    outlines: list[str] = []
    for index, bucket in enumerate(sorted(groups), start=1):
        group = groups[bucket]
        start = group[0].start_chapter
        end = group[-1].end_chapter
        arc_digest = _sha256_text(_json_text([arc.model_dump(mode="json") for arc in group]))
        path = workspace.structures_dir / ("volume_" + str(index).zfill(3) + ".json")
        cached = workspace.read_json(path)
        if (
            isinstance(cached, dict)
            and cached.get("source_sha256") == source_sha256
            and cached.get("arc_digest") == arc_digest
        ):
            outline = str(cached.get("outline") or "")
        else:
            outline = await _complete_text(
                author,
                _structure_prompt(document.title, start, end, group),
                "阶段 " + str(index) + " 结构",
                6000,
            )
            workspace.write_json(
                path,
                {
                    "source_sha256": source_sha256,
                    "arc_digest": arc_digest,
                    "start": start,
                    "end": end,
                    "outline": outline,
                },
            )
            workspace.write_text(
                workspace.structures_dir / ("volume_" + str(index).zfill(3) + ".md"),
                outline,
            )
        outlines.append(
            "【阶段" + str(index) + "：第" + str(start) + "-" + str(end) + "章】\n" + outline
        )
        manifest.setdefault("stages", {})["volume_structures_completed"] = index
        workspace.save_manifest(manifest)

    arcs_digest = _sha256_text(_json_text([arc.model_dump(mode="json") for arc in arcs]))
    novel_path = workspace.structures_dir / "novel_outline.json"
    cached_novel = workspace.read_json(novel_path)
    if (
        isinstance(cached_novel, dict)
        and cached_novel.get("source_sha256") == source_sha256
        and cached_novel.get("arcs_digest") == arcs_digest
    ):
        novel_outline = str(cached_novel.get("outline") or "")
    else:
        novel_outline = await _complete_text(
            author,
            _novel_prompt(document.title, outlines),
            "全书结构",
            7000,
        )
        workspace.write_json(
            novel_path,
            {
                "source_sha256": source_sha256,
                "arcs_digest": arcs_digest,
                "outline": novel_outline,
            },
        )
        workspace.write_text(workspace.structures_dir / "novel_outline.md", novel_outline)
    return outlines, novel_outline


def _ensure_entity(
    records: dict[str, dict[str, Any]],
    name: str,
    kind: str = "other",
    description: str = "",
) -> None:
    key = name.casefold()
    if key not in records:
        records[key] = {
            "name": name,
            "kind": kind,
            "description": description,
            "attributes": {},
        }


def _build_bundle(
    document: SourceDocument,
    cards: Sequence[ChapterCard],
    arcs: Sequence[StoryArcRecord],
    world_document: str,
    novel_outline: str,
    story_id: str | None,
    title: str | None,
    model: str,
) -> StoryBundle:
    entity_records: dict[str, dict[str, Any]] = {}
    source_by_entity: dict[str, set[str]] = {}
    for card in cards:
        for entity in card.entities:
            name = str(entity.get("name") or "").strip()
            if not name:
                continue
            _ensure_entity(
                entity_records,
                name,
                str(entity.get("kind") or "other"),
                _compact(entity.get("description") or entity.get("summary"), 1800),
            )
            key = name.casefold()
            source_by_entity.setdefault(key, set()).add(card.source_ref)
            if len(str(entity.get("description") or "")) > len(
                str(entity_records[key].get("description") or "")
            ):
                entity_records[key]["description"] = _compact(entity.get("description"), 1800)
        for relation in card.relationships:
            _ensure_entity(entity_records, relation["source"])
            _ensure_entity(entity_records, relation["target"])

    entity_ids: dict[str, str] = {}
    used_ids: set[str] = set()
    entities: list[StoryEntity] = []
    for key, item in entity_records.items():
        base = _slug(item["name"]) or "entity"
        entity_id = base
        suffix = 2
        while entity_id in used_ids:
            entity_id = base + "-" + str(suffix)
            suffix += 1
        used_ids.add(entity_id)
        entity_ids[key] = entity_id
        attributes = dict(item.get("attributes") or {})
        attributes["source_refs"] = sorted(source_by_entity.get(key, set()))
        entities.append(
            StoryEntity(
                entity_id=entity_id,
                name=item["name"],
                kind=item["kind"],
                description=item["description"],
                attributes=attributes,
            )
        )

    fact_records: dict[str, dict[str, Any]] = {}
    source_by_fact: dict[str, set[str]] = {}
    for card in cards:
        for fact in card.facts:
            content = str(fact.get("content") or "").strip()
            if not content:
                continue
            key = content.casefold()
            fact_records.setdefault(key, fact)
            source_by_fact.setdefault(key, set()).add(card.source_ref)
    facts: list[CanonFact] = []
    fact_ids: dict[str, str] = {}
    for key, item in fact_records.items():
        content = str(item["content"])
        fact_id = "fact-" + (_slug(content)[:64] or _sha256_text(content)[:12])
        if fact_id in fact_ids.values():
            fact_id += "-" + _sha256_text(content)[:6]
        fact_ids[key] = fact_id
        try:
            visibility = FactVisibility(str(item.get("visibility") or "public"))
        except ValueError:
            visibility = FactVisibility.PUBLIC
        known_by = [
            entity_ids.get(name.casefold(), name) for name in _string_list(item.get("known_by"))
        ]
        facts.append(
            CanonFact(
                fact_id=fact_id,
                content=content,
                source_refs=sorted(source_by_fact[key]),
                visibility=visibility,
                known_by=known_by,
            )
        )

    relationships: list[Relationship] = []
    seen_relationships: set[tuple[str, str, str]] = set()
    for card in cards:
        for relation in card.relationships:
            source = entity_ids.get(relation["source"].casefold())
            target = entity_ids.get(relation["target"].casefold())
            if not source or not target:
                continue
            relationship_key = (source, target, relation["label"].casefold())
            if relationship_key in seen_relationships:
                continue
            seen_relationships.add(relationship_key)
            relationships.append(
                Relationship(
                    relationship_id="rel-" + _sha256_text("|".join(relationship_key))[:12],
                    source_entity_id=source,
                    target_entity_id=target,
                    label=relation["label"],
                    description=_compact(relation.get("description"), 800),
                )
            )

    active_arcs = list(arcs)
    if not active_arcs:
        active_arcs = [_fallback_arc([card], index) for index, card in enumerate(cards, start=1)]
    cards_by_number = {card.chapter: card for card in cards}
    plot_arcs: list[PlotArc] = []
    beats: list[StoryBeat] = []
    for index, arc in enumerate(active_arcs):
        arc_cards = [
            cards_by_number[number]
            for number in range(arc.start_chapter, arc.end_chapter + 1)
            if number in cards_by_number
        ]
        present_entities: list[str] = []
        available_clues: list[str] = []
        for card in arc_cards:
            for entity in card.entities:
                present_entity_id = entity_ids.get(str(entity.get("name") or "").casefold())
                if present_entity_id and present_entity_id not in present_entities:
                    present_entities.append(present_entity_id)
            for fact in card.facts:
                present_fact_id = fact_ids.get(str(fact.get("content") or "").casefold())
                if present_fact_id and present_fact_id not in available_clues:
                    available_clues.append(present_fact_id)
        next_id = ""
        if index + 1 < len(active_arcs):
            next_id = active_arcs[index + 1].arc_id
        choices: list[BeatChoice] = []
        if next_id:
            choices.append(
                BeatChoice(
                    choice_id="continue-" + str(index + 1).zfill(4),
                    text="Continue to the next compiled story arc.",
                    risk="low",
                    next_beat_id=next_id,
                )
            )
        beats.append(
            StoryBeat(
                beat_id=arc.arc_id,
                arc_id=arc.arc_id,
                title=arc.title,
                present_entities=present_entities,
                available_clues=available_clues,
                dramatic_goal=arc.narrative_function or "推进当前情节阶段。",
                pressure=arc.emotion_rhythm,
                narrative=_render_arc(arc),
                source_refs=arc.source_refs,
                choices=choices,
                terminal=not bool(next_id),
            )
        )
        plot_arcs.append(
            PlotArc(
                arc_id=arc.arc_id,
                title=arc.title,
                summary=arc.narrative_function or arc.structure,
                beat_ids=[arc.arc_id],
                source_refs=arc.source_refs,
            )
        )

    if len(beats) == 1:
        ending_id = (_slug(story_id or document.source_id) or "story") + "-ending"
        beats[0].choices = [
            BeatChoice(
                choice_id="continue-0001",
                text="Continue to the compiled ending.",
                risk="low",
                next_beat_id=ending_id,
            )
        ]
        beats[0].terminal = False
        beats.append(
            StoryBeat(
                beat_id=ending_id,
                arc_id=beats[0].arc_id,
                title="End of compiled story",
                dramatic_goal="Close the compiled source.",
                narrative="The compiled source reaches the end of the available material.",
                source_refs=[document.source_id + ":ending"],
                terminal=True,
            )
        )

    return StoryBundle(
        story_id=_slug(story_id or document.source_id),
        title=title or document.title,
        locale=document.locale,
        opening=novel_outline or beats[0].narrative,
        source=SourceManifest(
            kind=document.kind,
            label=document.title,
            sha256=document.sha256,
            source_refs=[chapter.source_ref for chapter in document.chapters[: len(cards)]],
        ),
        evidence=[
            SourceEvidence(
                ref_id=chapter.source_ref,
                label=chapter.title,
                location="chapter:" + str(chapter.ordinal).zfill(4),
                excerpt=chapter.text[:600],
            )
            for chapter in document.chapters[: len(cards)]
        ],
        entities=entities,
        relationships=relationships,
        canon_facts=facts,
        plot_arcs=plot_arcs,
        story_beats=beats,
        style_profile=StyleProfile(
            language=document.locale,
            point_of_view="source_inferred",
            tone="source-informed",
            constraints=[
                "Do not present unsupported inference as canon.",
                "Choices advance between source-referenced story arcs.",
            ],
        ),
        optional_rules={
            "compiler": "tari_harnessnovel_compatible",
            "pipeline_version": PIPELINE_VERSION,
            "compiler_model": model,
            "world_knowledge": world_document,
            "novel_outline": novel_outline,
            "world_knowledge_sections": list(WORLD_SECTIONS),
        },
    )


def _world_book(bundle: StoryBundle, world_document: str) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    uid = 0
    for section, content in _split_world(world_document).items():
        entries[str(uid)] = {
            "uid": uid,
            "key": [section],
            "keysecondary": [],
            "comment": "tari:public",
            "content": content,
            "constant": True,
            "selective": False,
            "order": 100,
            "position": 0,
            "disable": False,
        }
        uid += 1
    for entity in bundle.entities:
        if entity.description:
            entries[str(uid)] = {
                "uid": uid,
                "key": [entity.name],
                "keysecondary": [],
                "comment": "tari:public",
                "content": (entity.name + "（" + entity.kind + "）：" + entity.description),
                "constant": False,
                "selective": False,
                "order": 90,
                "position": 0,
                "disable": False,
            }
            uid += 1
    return {
        "name": bundle.title,
        "description": (
            "Generated by TARI source decomposition; source references remain in the Story Bundle."
        ),
        "scan_depth": 4,
        "entries": entries,
    }


async def compile_document(
    document: SourceDocument,
    workspace: StoryCompilationWorkspace,
    *,
    author: TextCompletionAuthor,
    story_id: str | None = None,
    title: str | None = None,
    target_chapters: int | None = None,
    parallelism: int = 4,
    chapter_chars: int = 20000,
    window_chapters: int = 8,
    max_arc_chapters: int = 12,
    world_batch_chapters: int = 12,
    volume_size: int = 40,
    rebuild: bool = False,
) -> tuple[StoryBundle, dict[str, Any]]:
    """Run chapter cards -> rolling arcs -> world knowledge -> structures."""
    workspace.ensure()
    manifest = workspace.load_manifest()
    target = min(target_chapters or len(document.chapters), len(document.chapters))
    if target < 1:
        raise ValueError("target_chapters must be greater than zero")
    if rebuild:
        workspace.reset_derived()
        manifest = {}
    old_hash = manifest.get("source_sha256")
    if old_hash and old_hash != document.sha256 and not rebuild:
        raise ValueError("source changed since the last compilation; use rebuild=True")
    old_version = manifest.get("pipeline_version")
    if old_version not in (None, PIPELINE_VERSION) and not rebuild:
        raise ValueError("workspace uses an older pipeline; use rebuild=True")
    compiler_settings = {
        "story_id": story_id or document.source_id,
        "title": title or document.title,
        "locale": document.locale,
        "target_chapters": target,
        "parallelism": parallelism,
        "chapter_chars": chapter_chars,
        "window_chapters": window_chapters,
        "max_arc_chapters": max_arc_chapters,
        "world_batch_chapters": world_batch_chapters,
        "volume_size": volume_size,
        "provider": getattr(author.settings, "provider", ""),
        "base_url": getattr(author.settings, "base_url", ""),
        "timeout": getattr(author.settings, "timeout", None),
        "temperature": getattr(author.settings, "temperature", None),
        "max_tokens": getattr(author.settings, "max_tokens", None),
        "model": author.settings.model,
    }
    settings_fingerprint = _settings_fingerprint(compiler_settings)
    legacy_settings_fingerprint = _legacy_settings_fingerprint(compiler_settings)
    manifest = workspace.load_manifest()
    old_settings_fingerprint = manifest.get("settings_fingerprint")
    stored_settings = manifest.get("settings")
    accepted_fingerprints = {settings_fingerprint, legacy_settings_fingerprint}
    if isinstance(stored_settings, dict) and (
        _settings_fingerprint(stored_settings) == settings_fingerprint
    ):
        # A workspace whose stored settings hash to the current fingerprint may
        # also have been recorded by the legacy (timeout-inclusive) algorithm.
        accepted_fingerprints.add(_legacy_settings_fingerprint(stored_settings))
    if old_hash and not rebuild and old_settings_fingerprint not in accepted_fingerprints:
        if old_settings_fingerprint is None and manifest.get("stages", {}).get("source_plan"):
            manifest["settings_fingerprint"] = settings_fingerprint
            workspace.save_manifest(manifest)
            old_settings_fingerprint = settings_fingerprint
        else:
            raise ValueError("compilation settings changed; use rebuild=True")
    if old_hash and not rebuild and old_settings_fingerprint != settings_fingerprint:
        manifest["settings_fingerprint"] = settings_fingerprint
        workspace.save_manifest(manifest)

    manifest.update(
        {
            "pipeline_version": PIPELINE_VERSION,
            "source_id": document.source_id,
            "title": title or document.title,
            "source_sha256": document.sha256,
            "source_path": document.source_path,
            "encoding": document.encoding,
            "total_chapters": len(document.chapters),
            "target_chapters": target,
            "status": "in_progress",
            "settings": compiler_settings,
            "settings_fingerprint": settings_fingerprint,
            "stages": manifest.get("stages") or {},
        }
    )
    workspace.write_json(workspace.source_path, document.model_dump(mode="json"))
    workspace.save_manifest(manifest)

    cards = await _extract_cards(
        document, workspace, author, target, parallelism, chapter_chars, manifest
    )
    if len(cards) != target:
        raise RuntimeError("expected " + str(target) + " chapter cards, got " + str(len(cards)))
    manifest.setdefault("stages", {})["chapter_cards"] = True
    workspace.save_manifest(manifest)

    arcs = await _extract_arcs(
        workspace, cards, author, window_chapters, max_arc_chapters, manifest
    )
    manifest.setdefault("stages", {})["story_arcs"] = True
    workspace.save_manifest(manifest)

    world_document = await _extract_world(
        workspace, cards, author, world_batch_chapters, parallelism, manifest
    )
    manifest.setdefault("stages", {})["world_knowledge"] = True
    workspace.save_manifest(manifest)

    _, novel_outline = await _build_structures(
        workspace, document, arcs, author, volume_size, manifest
    )
    manifest.setdefault("stages", {}).update({"structures": True, "novel_outline": True})
    workspace.save_manifest(manifest)

    bundle = _build_bundle(
        document,
        cards,
        arcs,
        world_document,
        novel_outline,
        story_id,
        title,
        author.settings.model,
    )
    workspace.write_json(
        workspace.entities_path,
        [item.model_dump(mode="json") for item in bundle.entities],
    )
    workspace.write_json(
        workspace.facts_path,
        [item.model_dump(mode="json") for item in bundle.canon_facts],
    )
    workspace.write_json(
        workspace.relationships_path,
        [item.model_dump(mode="json") for item in bundle.relationships],
    )
    write_bundle(workspace.bundle_path, bundle)
    workspace.write_json(workspace.world_info_path, _world_book(bundle, world_document))
    manifest.update(
        {
            "status": "complete",
            "bundle_path": str(workspace.bundle_path),
            "world_info_path": str(workspace.world_info_path),
            "card_count": len(cards),
            "arc_count": len(arcs),
            "entity_count": len(bundle.entities),
            "fact_count": len(bundle.canon_facts),
            "relationship_count": len(bundle.relationships),
        }
    )
    workspace.save_manifest(manifest)
    return bundle, manifest


def compile_source(
    source: str | Path,
    *,
    output_dir: str | Path | None = None,
    story_id: str | None = None,
    title: str | None = None,
    lang: str = "en",
    max_chapters: int | None = None,
    settings: LLMSettings | None = None,
    author: TextCompletionAuthor | None = None,
    parallelism: int = 4,
    chapter_chars: int = 20000,
    window_chapters: int = 8,
    max_arc_chapters: int = 12,
    world_batch_chapters: int = 12,
    volume_size: int = 40,
    rebuild: bool = False,
    publish_world_path: str | Path | None = None,
) -> CompilationResult:
    """Parse a TXT/Markdown source and compile it into TARI resources."""
    source_path = Path(source)
    source_id = story_id or _slug(source_path.stem)
    workspace = StoryCompilationWorkspace(
        Path(output_dir or Path("runtime-data") / "story-books" / source_id)
    )
    from ..llm import OpenAICompatibleClient, resolve_llm_settings

    resolved = settings or resolve_llm_settings()
    active_author = cast(
        TextCompletionAuthor,
        author or OpenAICompatibleClient(resolved),
    )
    try:
        if rebuild:
            workspace.reset_derived()
        document, plan = asyncio.run(
            _prepare_source_document(
                source_path,
                workspace,
                author=active_author,
                source_id=source_id,
                title=title,
                locale=lang,
                rebuild=rebuild,
            )
        )
        if max_chapters is None:
            max_chapters = len(plan.chapters)
        bundle, manifest = asyncio.run(
            compile_document(
                document,
                workspace,
                author=active_author,
                story_id=story_id,
                title=title,
                target_chapters=max_chapters,
                parallelism=parallelism,
                chapter_chars=chapter_chars,
                window_chapters=window_chapters,
                max_arc_chapters=max_arc_chapters,
                world_batch_chapters=world_batch_chapters,
                volume_size=volume_size,
                rebuild=False,
            )
        )
    finally:
        if author is None:
            asyncio.run(active_author.aclose())
    if publish_world_path is not None:
        payload = workspace.read_json(workspace.world_info_path, {})
        target_path = Path(publish_world_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    chapter_count = int(manifest.get("target_chapters") or len(document.chapters))
    return CompilationResult(
        workspace=str(workspace.root),
        bundle_path=str(workspace.bundle_path),
        world_info_path=str(workspace.world_info_path),
        source_id=document.source_id,
        title=bundle.title,
        chapter_count=chapter_count,
        card_count=int(manifest.get("card_count") or 0),
        arc_count=int(manifest.get("arc_count") or 0),
        entity_count=int(manifest.get("entity_count") or 0),
        fact_count=int(manifest.get("fact_count") or 0),
        relationship_count=int(manifest.get("relationship_count") or 0),
        compiler=str(bundle.optional_rules.get("compiler") or ""),
        model=active_author.settings.model,
        manifest_path=str(workspace.manifest_path),
    )


__all__ = [
    "ChapterCard",
    "CompilationResult",
    "StoryArcRecord",
    "StoryCompilationWorkspace",
    "compile_document",
    "compile_source",
]
