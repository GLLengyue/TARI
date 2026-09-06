"""Typer-free entry points for the Story Mode end-to-end loop.

These helpers exist so tests, scripts, and downstream tools can drive the
``story-import`` -> ``story-new`` -> ``story-play`` -> ``story-branch``
flow without importing the Typer/rich CLI module (which would otherwise
require optional UI dependencies).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..story import (
    SourceDocument,
    StoryBundle,
    parse_source,
    scaffold_bundle,
    write_bundle,
)
from .author import FakeNarrativeAuthor, NarrativeAuthor
from .domain import CanonPolicy, PlayerIdentity, StorySessionState
from .runtime import NarrativeOrchestrator
from .storage import StoryStore

if TYPE_CHECKING:
    from ..llm import LLMSettings
    from ..story.decomposer import CompilationResult, TextCompletionAuthor


def import_bundle(
    source: str,
    output: str | None = None,
    *,
    story_id: str | None = None,
    title: str | None = None,
    lang: str = "en",
    max_chapters: int | None = None,
) -> tuple[Path, SourceDocument, StoryBundle]:
    document = parse_source(
        source,
        source_id=story_id,
        title=title,
        locale=lang,
        max_chapters=max_chapters,
    )
    bundle = scaffold_bundle(document, story_id=story_id, title=title)
    output_path = Path(output or (bundle.story_id + ".yaml"))
    write_bundle(output_path, bundle)
    return output_path, document, bundle


def compile_bundle(
    source: str | Path,
    output_dir: str | Path | None = None,
    *,
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
    """Compile a source novel into a resumable Story Mode workspace."""
    from ..story.decomposer import compile_source

    return compile_source(
        source,
        output_dir=output_dir,
        story_id=story_id,
        title=title,
        lang=lang,
        max_chapters=max_chapters,
        settings=settings,
        author=author,
        parallelism=parallelism,
        chapter_chars=chapter_chars,
        window_chapters=window_chapters,
        max_arc_chapters=max_arc_chapters,
        world_batch_chapters=world_batch_chapters,
        volume_size=volume_size,
        rebuild=rebuild,
        publish_world_path=publish_world_path,
    )


def _default_story_store() -> StoryStore:
    return StoryStore(os.getenv("TRPG_DB_PATH", "runtime-data/trpg.db"))


def _coerce_policy(value: str | CanonPolicy) -> CanonPolicy:
    if isinstance(value, CanonPolicy):
        return value
    return CanonPolicy(str(value).strip().lower())


def create_session(
    bundle_path: str,
    session_id: str | None = None,
    *,
    identity: PlayerIdentity | None = None,
    canon_policy: str | CanonPolicy = CanonPolicy.GUIDED,
    author: NarrativeAuthor | None = None,
    store: StoryStore | None = None,
    seed: int = 0,
) -> tuple[StoryBundle, StorySessionState]:
    from ..story import load_bundle as _load_bundle

    story_bundle = _load_bundle(bundle_path)
    runtime = NarrativeOrchestrator(
        store or _default_story_store(),
        story_bundle,
        author or FakeNarrativeAuthor(),
    )
    state = asyncio.run(
        runtime.start_session(
            identity or PlayerIdentity(display_name="Player"),
            session_id=session_id,
            seed=seed,
            canon_policy=_coerce_policy(canon_policy),
        )
    )
    return story_bundle, state


def branch_session(
    session_id: str,
    branch_id: str,
    *,
    from_branch: str = "main",
    store: StoryStore | None = None,
) -> StorySessionState:
    s = store or _default_story_store()
    parent = s.load_story_snapshot(session_id, from_branch)
    return s.create_story_branch(parent, branch_id)


__all__ = [
    "branch_session",
    "compile_bundle",
    "create_session",
    "import_bundle",
]
