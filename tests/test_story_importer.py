from __future__ import annotations

import hashlib

import pytest

from trpg_runtime.story import load_bundle, parse_source, scaffold_bundle, write_bundle


def test_markdown_source_preserves_chapters_hashes_and_evidence(tmp_path):
    source = tmp_path / "lantern.md"
    source.write_text(
        "# The Lantern\n\nA short preface.\n\n"
        "## Arrival\n\nThe gate opens at dusk.\n\n"
        "## The Archive\n\nThe register remembers the name.\n",
        encoding="utf-8",
    )

    document = parse_source(source, source_id="lantern-source")

    assert document.kind == "markdown"
    assert document.title == "The Lantern"
    assert [chapter.title for chapter in document.chapters] == ["Arrival", "The Archive"]
    assert document.chapters[0].text.startswith("A short preface.")
    assert document.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert document.chapters[0].sha256 == hashlib.sha256(
        document.chapters[0].text.encode("utf-8")
    ).hexdigest()
    assert document.chapters[0].source_ref == "lantern-source:chapter:001"

    bundle = scaffold_bundle(document)

    assert bundle.story_id == "lantern-source"
    assert bundle.optional_rules["compiler"] == "deterministic_scaffold"
    assert bundle.source.sha256 == document.sha256
    assert [beat.beat_id for beat in bundle.story_beats] == [
        "lantern-source-chapter-001",
        "lantern-source-chapter-002",
    ]
    assert bundle.story_beats[0].choices[0].next_beat_id == "lantern-source-chapter-002"
    assert bundle.story_beats[-1].terminal
    assert bundle.story_beats[-1].choices == []
    assert {item.ref_id for item in bundle.evidence} == {
        "lantern-source:chapter:001",
        "lantern-source:chapter:002",
    }


def test_text_source_can_be_limited_and_round_tripped_as_json(tmp_path):
    source = tmp_path / "story.txt"
    source.write_text(
        "Chapter 1\nFirst scene.\n\nChapter 2\nSecond scene.\n\nChapter 3\nThird scene.",
        encoding="utf-8",
    )

    document = parse_source(source, source_id="story", max_chapters=2)
    bundle = scaffold_bundle(document, story_id="story-demo", title="Story Demo")
    output = tmp_path / "story.json"
    write_bundle(output, bundle)
    restored = load_bundle(output)

    assert document.kind == "text"
    assert len(document.chapters) == 2
    assert restored == bundle
    assert restored.title == "Story Demo"
    assert restored.story_beats[-1].terminal


def test_source_parser_rejects_unsupported_and_empty_inputs(tmp_path):
    unsupported = tmp_path / "story.pdf"
    unsupported.write_bytes(b"not text")
    with pytest.raises(ValueError, match=r"\.txt"):
        parse_source(unsupported)

    empty = tmp_path / "empty.txt"
    empty.write_text("   \n", encoding="utf-8")
    with pytest.raises(ValueError, match="no non-empty chapters"):
        parse_source(empty)

    with pytest.raises(ValueError, match="greater than zero"):
        parse_source(unsupported, max_chapters=0)
