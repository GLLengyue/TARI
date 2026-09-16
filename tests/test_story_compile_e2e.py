"""Opt-in full-pipeline E2E: raw novel -> semantic compile -> playable bundle.

This is the reproducible version of the clean-room Odyssey acceptance run. It
is gated behind ``TARI_E2E_COMPILE=1`` because it needs a real LLM endpoint and
takes minutes. Assertions cover structural invariants only — never entity
counts, model prose, or provider identity.

Usage::

    TARI_E2E_COMPILE=1 TARI_LLM_BASE_URL=... TARI_LLM_API_KEY=... \
        TARI_LLM_MODEL=... .venv/bin/python -m pytest tests/test_story_compile_e2e.py -q

Optional knobs:

- ``TARI_E2E_SOURCE``: path to the source document. Defaults to the bundled
  Odyssey source under ``runtime-data/sources/odyssey/``.
- ``TARI_E2E_MAX_CHAPTERS``: truncate compilation to N chapters for a shorter
  acceptance pass; the invariants hold at any target size.

Provider-side failures (e.g. a chapter card whose JSON is truncated at the
``max_tokens`` cap) make a compile stage fail with its checkpoint intact; the
documented recovery path is to simply re-run the compile. The test therefore
drives up to three compile attempts and asserts the workspace still reaches
``complete`` — recovery is part of the acceptance contract, not a workaround.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

import trpg_runtime.narrative.workflow as workflow
from trpg_runtime.llm import OpenAICompatibleClient, resolve_llm_settings
from trpg_runtime.narrative import (
    NarrativeInput,
    NarrativeOrchestrator,
    OpenAINarrativeAuthor,
    PlayerIdentity,
    StoryStore,
)
from trpg_runtime.story import load_bundle

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO_ROOT / "runtime-data" / "sources" / "odyssey" / "odyssey.txt"

STAGE_FLAGS = (
    "source_plan",
    "chapter_cards",
    "story_arcs",
    "world_knowledge",
    "structures",
    "novel_outline",
)


class _CountingAuthor:
    """Delegate that records provider calls so resume can assert zero."""

    def __init__(self, inner: OpenAICompatibleClient) -> None:
        self._inner = inner
        self.settings = inner.settings
        self.calls = 0

    async def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.calls += 1
        return await self._inner.complete_text(
            messages, temperature=temperature, max_tokens=max_tokens
        )

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        return await self._inner.complete_json(
            messages, temperature=temperature, max_tokens=max_tokens
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


@pytest.mark.skipif(
    os.environ.get("TARI_E2E_COMPILE") != "1",
    reason=(
        "set TARI_E2E_COMPILE=1 and TARI_LLM_* / EVOT_LLM_* to run "
        "the full source-to-bundle compile E2E"
    ),
)
def test_full_compile_pipeline_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real LLM compile: source -> workspace -> valid bundle -> one played turn."""
    settings = resolve_llm_settings()
    if not settings.is_configured:
        pytest.skip("no LLM endpoint configured")

    source = Path(os.environ.get("TARI_E2E_SOURCE") or DEFAULT_SOURCE)
    if not source.is_file():
        pytest.skip(f"source document not found: {source}")

    max_chapters_env = os.environ.get("TARI_E2E_MAX_CHAPTERS")
    max_chapters = int(max_chapters_env) if max_chapters_env else None

    # Detached clean-room: copy the source and keep every artifact in tmp_path.
    detached_source = tmp_path / source.name
    shutil.copyfile(source, detached_source)
    source_sha = hashlib.sha256(detached_source.read_bytes()).hexdigest()

    db = tmp_path / "story.db"
    monkeypatch.setenv("TRPG_DB_PATH", str(db))

    client = OpenAICompatibleClient(settings)
    counting = _CountingAuthor(client)
    workspace = tmp_path / "workspace"
    try:
        result = None
        last_error: RuntimeError | None = None
        for _attempt in range(3):
            try:
                result = workflow.compile_bundle(
                    detached_source,
                    workspace,
                    story_id="e2e-compile",
                    settings=settings,
                    author=counting,
                    max_chapters=max_chapters,
                )
                break
            except RuntimeError as exc:
                # Stage failures leave resumable checkpoints; re-running the
                # compile is the documented recovery path.
                last_error = exc
        if result is None:
            raise last_error or AssertionError("compile did not run")
        assert counting.calls > 0, "compile unexpectedly skipped the provider"

        manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
        assert manifest["status"] == "complete"
        assert manifest.get("failures", {}) == {}
        for stage in STAGE_FLAGS:
            assert manifest["stages"].get(stage) is True, stage
        assert manifest["source_sha256"] == source_sha
        assert manifest["settings_fingerprint"]

        plan = json.loads((workspace / "source_plan.json").read_text(encoding="utf-8"))
        chapters = plan["chapters"]
        assert chapters
        assert [c["ordinal"] for c in chapters] == list(range(1, len(chapters) + 1))
        for chapter in chapters:
            assert chapter["start_line"] <= chapter["end_line"]

        target = min(max_chapters or len(chapters), len(chapters))
        card_files = sorted((workspace / "chapter_cards").glob("*.json"))
        assert len(card_files) == target == result.chapter_count == result.card_count
        assert result.arc_count >= 1

        world_info = json.loads(Path(result.world_info_path).read_text(encoding="utf-8"))
        assert world_info["entries"]

        # The shipped loader validates every bundle reference — this is the
        # acceptance check, not a secondary parser.
        bundle = load_bundle(result.bundle_path)
        assert bundle.entities
        assert bundle.canon_facts
        assert bundle.relationships
        assert bundle.story_beats
        assert {item.ref_id for item in bundle.evidence} >= set(bundle.source.source_refs)
        assert bundle.first_beat.choices, "first beat must offer at least one choice"

        # A warm workspace must resume without re-calling the provider.
        counting.calls = 0
        resumed = workflow.compile_bundle(
            detached_source,
            workspace,
            story_id="e2e-compile",
            settings=settings,
            author=counting,
            max_chapters=max_chapters,
        )
        assert counting.calls == 0, "cached compile re-called the provider"
        assert resumed.bundle_path == result.bundle_path
    finally:
        asyncio.run(client.aclose())

    # The compiled bundle must be playable through the real runtime path.
    store = StoryStore(db)
    author = OpenAINarrativeAuthor(settings)
    runtime = NarrativeOrchestrator(store, bundle, author)
    session_id = "e2e-compile-" + str(int(time.time()))
    try:
        _, state = workflow.create_session(
            str(result.bundle_path),
            session_id=session_id,
            identity=PlayerIdentity(display_name="E2E Reader", identity_type="visitor"),
            canon_policy="guided",
            author=author,
            store=store,
        )
        state, turn = asyncio.run(
            runtime.process_turn(
                state,
                NarrativeInput(
                    choice_id=bundle.first_beat.choices[0].choice_id,
                    input_mode="choice",
                ),
                request_id="e2e-compile-turn-1",
            )
        )
        assert turn.narrative.strip()
        assert turn.narrative_beat_id
    finally:
        asyncio.run(author.aclose())
