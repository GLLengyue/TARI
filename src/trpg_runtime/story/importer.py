from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .bundle import (
    BeatChoice,
    PlotArc,
    SourceEvidence,
    SourceManifest,
    StoryBeat,
    StoryBundle,
    StyleProfile,
)


class SourceChapter(BaseModel):
    chapter_id: str
    ordinal: int
    title: str
    text: str
    source_ref: str
    sha256: str


class SourceDocument(BaseModel):
    source_id: str
    title: str
    kind: Literal["text", "markdown"]
    locale: str = "en"
    source_path: str | None = None
    sha256: str
    chapters: list[SourceChapter] = Field(min_length=1)


_MARKDOWN_HEADING = re.compile(
    r"^\s*(?P<marks>#{1,6})\s+(?P<title>.+?)\s*#*\s*$"
)
_PLAIN_CHAPTER = re.compile(
    r"^\s*(?:chapter\s+[\w.-]+|part\s+[\w.-]+|volume\s+[\w.-]+|"
    r"第\s*[^\n]{1,30}[章节卷回]|(?:序章|楔子|尾声|终章)|"
    r"\d+[.、:：]\s*\S.*)$",
    re.IGNORECASE,
)


def _slug(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value.strip().lower())
    return value.strip("-") or "source"


def _chapter_title(value: str, ordinal: int) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().strip("#")).strip()
    return normalized or "Chapter " + str(ordinal)


def _first_markdown_title(lines: list[str]) -> str | None:
    for line in lines:
        match = _MARKDOWN_HEADING.match(line)
        if match and len(match.group("marks")) == 1:
            return _chapter_title(match.group("title"), 1)
        if line.strip() and not line.lstrip().startswith("#"):
            break
    return None


def _parse_chapters(text: str, default_title: str) -> tuple[str, list[tuple[str, str]]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    document_title = _first_markdown_title(lines) or default_title
    current_title: str | None = None
    current_lines: list[str] = []
    preamble: list[str] = []
    sections: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        if current_title is not None:
            body = "\n".join(current_lines).strip()
            if body:
                sections.append((current_title, body))
        current_title = None
        current_lines = []

    for line in lines:
        markdown = _MARKDOWN_HEADING.match(line)
        if markdown:
            level = len(markdown.group("marks"))
            heading = _chapter_title(markdown.group("title"), len(sections) + 1)
            if level == 1 and current_title is None and not sections:
                document_title = heading
                continue
            if level >= 2:
                flush()
                current_title = heading
                continue
            if current_title is None:
                preamble.append(line)
            else:
                current_lines.append(line)
            continue

        if _PLAIN_CHAPTER.match(line):
            flush()
            current_title = _chapter_title(line, len(sections) + 1)
            continue

        if current_title is None:
            preamble.append(line)
        else:
            current_lines.append(line)

    flush()
    if not sections:
        body = text.strip()
        if body:
            sections = [("Chapter 1", body)]
    elif "\n".join(preamble).strip():
        prefix = "\n".join(preamble).strip()
        first_title, first_body = sections[0]
        sections[0] = (first_title, prefix + "\n\n" + first_body)
    return document_title, sections


def parse_source(
    path: str | Path,
    *,
    source_id: str | None = None,
    title: str | None = None,
    locale: str = "en",
    max_chapters: int | None = None,
) -> SourceDocument:
    """Parse a local UTF-8 TXT/Markdown source into hashed chapter evidence."""
    source_path = Path(path)
    if max_chapters is not None and max_chapters < 1:
        raise ValueError("max_chapters must be greater than zero")
    if source_path.suffix.lower() not in {".txt", ".md", ".markdown"}:
        raise ValueError("source must be a .txt, .md, or .markdown file")

    raw = source_path.read_bytes()
    text = raw.decode("utf-8")
    default_title = title or source_path.stem.replace("_", " ").replace("-", " ").strip()
    document_title, sections = _parse_chapters(text, default_title or "Imported Story")
    if max_chapters is not None:
        sections = sections[:max_chapters]
    if not sections:
        raise ValueError("source contains no non-empty chapters")

    normalized_source_id = _slug(source_id or source_path.stem)
    kind: Literal["text", "markdown"] = (
        "markdown" if source_path.suffix.lower() in {".md", ".markdown"} else "text"
    )
    chapters = []
    for index, (chapter_title, chapter_text) in enumerate(sections, start=1):
        ordinal = str(index).zfill(3)
        chapters.append(
            SourceChapter(
                chapter_id=normalized_source_id + "-chapter-" + ordinal,
                ordinal=index,
                title=_chapter_title(chapter_title, index),
                text=chapter_text,
                source_ref=normalized_source_id + ":chapter:" + ordinal,
                sha256=hashlib.sha256(chapter_text.encode("utf-8")).hexdigest(),
            )
        )

    return SourceDocument(
        source_id=normalized_source_id,
        title=document_title,
        kind=kind,
        locale=locale,
        source_path=str(source_path),
        sha256=hashlib.sha256(raw).hexdigest(),
        chapters=chapters,
    )


def scaffold_bundle(
    document: SourceDocument,
    *,
    story_id: str | None = None,
    title: str | None = None,
) -> StoryBundle:
    """Create a deterministic, source-preserving Story Bundle scaffold.

    This is intentionally not semantic novel analysis. Each source chapter is
    one beat, and the only generated decision is ``continue``. It gives the
    runtime a real, auditable bundle while leaving entity/fact/plot extraction
    for a later model-backed compiler.
    """
    arc_id = "source-excerpt"
    bundle_title = title or document.title
    bundle_story_id = _slug(story_id or document.source_id)
    chapters = list(document.chapters)
    beat_count = len(chapters)
    terminal_id = bundle_story_id + "-ending"
    beat_ids = [chapter.chapter_id for chapter in chapters]
    if beat_count == 1:
        beat_ids.append(terminal_id)

    next_beat_ids: list[str] = []
    for index in range(beat_count):
        if beat_count == 1:
            next_beat_ids.append(terminal_id)
        elif index < beat_count - 1:
            next_beat_ids.append(chapters[index + 1].chapter_id)
        else:
            next_beat_ids.append("")

    beats: list[StoryBeat] = []
    for index, chapter in enumerate(chapters):
        next_beat_id = next_beat_ids[index]
        is_terminal = not next_beat_id
        choices: list[BeatChoice] = []
        if not is_terminal:
            choices.append(
                BeatChoice(
                    choice_id="continue-" + str(chapter.ordinal).zfill(3),
                    text="Continue to the next imported chapter.",
                    risk="low",
                    next_beat_id=next_beat_id,
                )
            )
        beats.append(
            StoryBeat(
                beat_id=chapter.chapter_id,
                arc_id=arc_id,
                title=chapter.title,
                dramatic_goal="Explore the imported source chapter without changing its text.",
                narrative=chapter.text,
                source_refs=[chapter.source_ref],
                choices=choices,
                terminal=is_terminal,
            )
        )

    if beat_count == 1:
        beats.append(
            StoryBeat(
                beat_id=terminal_id,
                arc_id=arc_id,
                title="End of imported excerpt",
                dramatic_goal="Close the imported source excerpt.",
                narrative="The imported excerpt ends here.",
                source_refs=[document.source_id + ":ending"],
                choices=[],
                terminal=True,
            )
        )

    evidence = [
        SourceEvidence(
            ref_id=chapter.source_ref,
            label=chapter.title,
            location="chapter:" + str(chapter.ordinal).zfill(3),
            excerpt=chapter.text[:500],
        )
        for chapter in chapters
    ]
    if beat_count == 1:
        evidence.append(
            SourceEvidence(
                ref_id=document.source_id + ":ending",
                label="End of imported excerpt",
                location="generated",
                excerpt="Generated terminal beat for the deterministic scaffold.",
            )
        )

    return StoryBundle(
        story_id=bundle_story_id,
        title=bundle_title,
        locale=document.locale,
        opening=(
            "Imported source: "
            + bundle_title
            + ".\n\n"
            + chapters[0].text[:1200]
        ),
        source=SourceManifest(
            kind=document.kind,
            label=document.title,
            sha256=document.sha256,
            source_refs=[chapter.source_ref for chapter in chapters],
        ),
        evidence=evidence,
        plot_arcs=[
            PlotArc(
                arc_id=arc_id,
                title="Imported source excerpt",
                summary="A deterministic chapter-by-chapter scaffold.",
                beat_ids=beat_ids,
                source_refs=[chapter.source_ref for chapter in chapters],
            )
        ],
        story_beats=beats,
        style_profile=StyleProfile(
            language=document.locale,
            point_of_view="source_preserved",
            tone="source",
            constraints=[
                "This scaffold preserves source chapter text; it is not semantic rewriting."
            ],
        ),
        optional_rules={
            "compiler": "deterministic_scaffold",
            "source_chapter_count": len(chapters),
        },
    )
