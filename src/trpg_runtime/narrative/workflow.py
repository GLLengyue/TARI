"""Typer-free entry points for the Story Mode end-to-end loop.

These helpers exist so tests, scripts, and downstream tools can drive the
``story-import`` -> ``story-new`` -> ``story-play`` -> ``story-branch``
flow without importing the Typer/rich CLI module (which would otherwise
require optional UI dependencies).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .author import FakeNarrativeAuthor, NarrativeAuthor
from .domain import CanonPolicy, PlayerIdentity, StorySessionState
from .runtime import NarrativeOrchestrator
from .storage import StoryStore

from ..story import (
    SourceDocument,
    StoryBundle,
    parse_source,
    scaffold_bundle,
    write_bundle,
)


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
        store or StoryStore(),
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
    s = store or StoryStore()
    parent = s.load_story_snapshot(session_id, from_branch)
    return s.create_story_branch(parent, branch_id)


__all__ = [
    "branch_session",
    "create_session",
    "import_bundle",
]
