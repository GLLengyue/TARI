"""Fault-injection contracts for the Story context (M2).

These tests pin the reliability semantics that were previously only implicit:
bundle schema versioning, turn-transaction atomicity/conflict behaviour,
request-id idempotency scope, branch fork-point projection, and compiler
checkpoint corruption recovery. Everything here is deterministic — no real
LLM, no network.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from test_story_compiler import FakeCompilerAuthor, NoCallCompilerAuthor

from trpg_runtime.narrative import (
    CanonPolicy,
    FakeNarrativeAuthor,
    NarrativeInput,
    NarrativeOrchestrator,
    PlayerIdentity,
    StoryStore,
    compile_bundle,
)
from trpg_runtime.rules import RuleViolation
from trpg_runtime.story import load_bundle, write_bundle
from trpg_runtime.story.bundle import BUNDLE_SCHEMA_VERSION

BUNDLE_PATH = "examples/story/lantern_gate.yaml"

LANTERN_SOURCE = (
    "# The Lantern Gate\n\n"
    "## Arrival\n\nAri reaches the gate at dusk.\n\n"
    "## The Clue\n\nAri finds a mark beneath the lantern.\n"
)


def _runtime(tmp_path: Path):
    bundle = load_bundle(BUNDLE_PATH)
    store = StoryStore(tmp_path / "story.db")
    runtime = NarrativeOrchestrator(store, bundle, FakeNarrativeAuthor())
    return bundle, store, runtime


def _start(tmp_path: Path, session_id: str = "lantern-rel"):
    bundle, store, runtime = _runtime(tmp_path)
    state = asyncio.run(
        runtime.start_session(
            PlayerIdentity(display_name="Ari", identity_type="visitor"),
            session_id=session_id,
            seed=7,
            canon_policy=CanonPolicy.GUIDED,
        )
    )
    return bundle, store, runtime, state


def _turn(runtime, state, request_id: str | None = None):
    choice = state.available_choices[0]
    return asyncio.run(
        runtime.process_turn(
            state,
            NarrativeInput(choice_id=choice.choice_id, input_mode="choice"),
            request_id=request_id,
        )
    )


def _compile_lantern(tmp_path: Path, author):
    source = tmp_path / "lantern.md"
    if not source.exists():
        source.write_text(LANTERN_SOURCE, encoding="utf-8")
    workspace = tmp_path / "compiled"
    result = compile_bundle(
        source,
        workspace,
        story_id="lantern-rel",
        author=author,
        parallelism=2,
        window_chapters=2,
        max_arc_chapters=2,
        world_batch_chapters=2,
    )
    return result, workspace


class CountingCompilerAuthor(FakeCompilerAuthor):
    """FakeCompilerAuthor that records how often the model was invoked."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete_text(self, messages, *, temperature=None, max_tokens=None):
        self.calls += 1
        return await super().complete_text(messages, temperature=temperature, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Bundle schema version policy
# ---------------------------------------------------------------------------


def _write_bundle_doc(tmp_path: Path, mutate) -> Path:
    document = yaml.safe_load(Path(BUNDLE_PATH).read_text(encoding="utf-8"))
    mutate(document)
    path = tmp_path / "mutated.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


def test_bundle_rejects_unknown_schema_version(tmp_path: Path) -> None:
    path = _write_bundle_doc(tmp_path, lambda doc: doc.update({"schema_version": 99}))
    with pytest.raises(ValueError, match="schema version"):
        load_bundle(path)


def test_bundle_missing_schema_version_defaults_to_supported(tmp_path: Path) -> None:
    path = _write_bundle_doc(tmp_path, lambda doc: doc.pop("schema_version", None))
    bundle = load_bundle(path)
    assert bundle.schema_version == BUNDLE_SCHEMA_VERSION


def test_write_bundle_roundtrips_schema_version(tmp_path: Path) -> None:
    bundle = load_bundle(BUNDLE_PATH)
    out = tmp_path / "roundtrip.yaml"
    write_bundle(out, bundle)
    document = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert document["schema_version"] == BUNDLE_SCHEMA_VERSION
    assert load_bundle(out).schema_version == BUNDLE_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Turn transaction atomicity and request-id semantics
# ---------------------------------------------------------------------------


def test_uncommitted_transaction_persists_nothing(tmp_path: Path) -> None:
    _, store, _, state = _start(tmp_path)
    events_before = store.story_events(state.session_id)

    tx = store.begin_story_turn(state.session_id, state.branch_id, state.turn_number + 1)
    tx.append("story_narrative_emitted", {"narrative": "never committed"})
    del tx  # crash between begin and commit: neither commit nor abort ran

    assert store.story_events(state.session_id) == events_before
    assert store.load_story_snapshot(state.session_id).turn_number == state.turn_number


def test_aborted_turn_persists_only_audit_event(tmp_path: Path) -> None:
    _, store, _, state = _start(tmp_path)
    events_before = store.story_events(state.session_id)

    tx = store.begin_story_turn(state.session_id, state.branch_id, state.turn_number + 1)
    tx.append("story_narrative_emitted", {"narrative": "dropped"})
    tx.abort("author exploded")

    events = store.story_events(state.session_id)
    assert len(events) == len(events_before) + 1
    audit = events[-1]
    assert audit["type"] == "story_turn_aborted"
    assert audit["payload"]["error"] == "author exploded"
    assert audit["payload"]["events"] == ["story_narrative_emitted"]
    assert store.load_story_snapshot(state.session_id).turn_number == state.turn_number


def test_interrupted_turn_recovers_last_committed_state(tmp_path: Path) -> None:
    _, store, runtime, state = _start(tmp_path)
    state, _ = _turn(runtime, state, request_id="committed-turn")

    tx = store.begin_story_turn(state.session_id, state.branch_id, state.turn_number + 1)
    tx.append("story_narrative_emitted", {"narrative": "half-written"})
    del tx

    # A fresh process on the same database must see only the committed state.
    reopened = StoryStore(tmp_path / "story.db")
    snapshot = reopened.load_story_snapshot(state.session_id)
    assert snapshot.turn_number == state.turn_number
    assert snapshot.version == state.version
    events = reopened.story_events(state.session_id)
    assert all(event["type"] != "story_narrative_emitted" or event["turn"] <= 1 for event in events)


def test_duplicate_request_id_commit_fails_without_partial_state(tmp_path: Path) -> None:
    _, store, runtime, state = _start(tmp_path)
    state, result = _turn(runtime, state, request_id="req-dup")
    events_after = store.story_events(state.session_id)

    # Bypass the runtime idempotency check and force a second commit carrying
    # the same request_id — the store must reject it and leave nothing behind.
    tx = store.begin_story_turn(state.session_id, "main", state.turn_number + 1)
    tx.append("story_narrative_emitted", {"narrative": "rogue duplicate"})
    rogue_state = state.model_copy(
        update={"turn_number": state.turn_number + 1, "version": state.version + 1}
    )
    duplicate_result = result.model_copy(update={"turn_number": rogue_state.turn_number})
    with pytest.raises(ValueError, match="conflicting story turn commit"):
        tx.commit(rogue_state, request_id="req-dup", result=duplicate_result)

    assert store.story_events(state.session_id) == events_after
    assert store.load_story_snapshot(state.session_id).turn_number == state.turn_number
    assert store.load_story_turn_result("req-dup") == result


def test_request_id_replay_returns_first_result_regardless_of_payload(tmp_path: Path) -> None:
    """request_id is a dedup key: replaying it with a different input still
    returns the originally committed result and touches nothing."""
    _, store, runtime, state = _start(tmp_path)
    first_choice = state.available_choices[0].choice_id
    state, first = _turn(runtime, state, request_id="req-replay")
    events_after = store.story_events(state.session_id)

    other_choice = next(c for c in state.available_choices if c.choice_id != first_choice)
    replayed_state, second = asyncio.run(
        runtime.process_turn(
            state,
            NarrativeInput(choice_id=other_choice.choice_id, input_mode="choice"),
            request_id="req-replay",
        )
    )

    assert second == first
    assert replayed_state == state
    assert store.story_events(state.session_id) == events_after


def test_request_id_conflicts_across_branches(tmp_path: Path) -> None:
    """request_id rows are global keys; reuse on another branch is rejected."""
    _, store, runtime, state = _start(tmp_path)
    state, _ = _turn(runtime, state, request_id="req-branch")
    child = store.create_story_branch(state, "alt")

    with pytest.raises(RuleViolation, match="different story branch"):
        asyncio.run(
            runtime.process_turn(
                child,
                NarrativeInput(
                    choice_id=child.available_choices[0].choice_id,
                    input_mode="choice",
                ),
                request_id="req-branch",
            )
        )


def test_child_branch_projection_freezes_at_fork_turn(tmp_path: Path) -> None:
    """Ancestor events project only up to the recorded fork turn; turns the
    parent plays afterwards stay invisible to the child."""
    _, store, runtime, state = _start(tmp_path)
    state, _ = _turn(runtime, state)
    state, _ = _turn(runtime, state)
    fork_turn = state.turn_number
    child = store.create_story_branch(state, "alt")
    state, _ = _turn(runtime, state)  # parent advances past the fork

    child_events = store.story_events(state.session_id, "alt")
    inherited = [event for event in child_events if event["branch_id"] == "main"]
    assert inherited
    assert all(event["turn"] <= fork_turn for event in inherited)
    assert not any(event["turn"] > fork_turn for event in child_events)

    parent_events = store.story_events(state.session_id, "main")
    assert any(event["turn"] == fork_turn + 1 for event in parent_events)
    assert child.parent_branch_id == "main"


# ---------------------------------------------------------------------------
# Compiler checkpoint corruption recovery
# ---------------------------------------------------------------------------


def test_corrupt_chapter_card_is_regenerated(tmp_path: Path) -> None:
    result, workspace = _compile_lantern(tmp_path, FakeCompilerAuthor())
    (workspace / "chapter_cards" / "chapter_0002.json").write_text("{ not json", encoding="utf-8")

    counting = CountingCompilerAuthor()
    resumed, _ = _compile_lantern(tmp_path, counting)
    assert counting.calls == 1  # only the damaged chapter is re-extracted
    assert resumed.bundle_path == result.bundle_path
    assert json.loads((workspace / "manifest.json").read_text())["status"] == "complete"


def test_card_with_stale_source_sha_is_regenerated(tmp_path: Path) -> None:
    result, workspace = _compile_lantern(tmp_path, FakeCompilerAuthor())
    card_path = workspace / "chapter_cards" / "chapter_0001.json"
    payload = json.loads(card_path.read_text(encoding="utf-8"))
    payload["source_sha256"] = "deadbeef" * 8
    card_path.write_text(json.dumps(payload), encoding="utf-8")

    counting = CountingCompilerAuthor()
    resumed, _ = _compile_lantern(tmp_path, counting)
    assert counting.calls == 1
    assert resumed.bundle_path == result.bundle_path


def test_corrupt_source_plan_regenerates_only_the_plan(tmp_path: Path) -> None:
    result, workspace = _compile_lantern(tmp_path, FakeCompilerAuthor())
    (workspace / "source_plan.json").write_text("{ not json", encoding="utf-8")

    counting = CountingCompilerAuthor()
    resumed, _ = _compile_lantern(tmp_path, counting)
    # The plan is re-derived once; every later stage still hits its cache.
    assert counting.calls == 1
    assert resumed.bundle_path == result.bundle_path


def test_corrupt_arcs_state_reruns_only_the_arc_stage(tmp_path: Path) -> None:
    result, workspace = _compile_lantern(tmp_path, FakeCompilerAuthor())
    (workspace / "story_arcs" / "arcs_state.json").write_text("{ not json", encoding="utf-8")

    counting = CountingCompilerAuthor()
    resumed, _ = _compile_lantern(tmp_path, counting)
    # One arc window for two chapters; downstream stages stay cached because
    # the deterministic fake regenerates identical arcs.
    assert counting.calls == 1
    assert resumed.bundle_path == result.bundle_path


def test_corrupt_manifest_resumes_from_artifact_caches(tmp_path: Path) -> None:
    result, workspace = _compile_lantern(tmp_path, FakeCompilerAuthor())
    (workspace / "manifest.json").write_text("{ not json", encoding="utf-8")

    # Every stage validates its own artifacts (SHA/digest/model), so a wiped
    # manifest must resume without re-calling the provider at all.
    resumed, _ = _compile_lantern(tmp_path, NoCallCompilerAuthor())
    assert resumed.bundle_path == result.bundle_path
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["status"] == "complete"
