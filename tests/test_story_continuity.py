"""Regression cases from the story-first review, without external providers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from trpg_runtime.narrative import (
    FakeNarrativeAuthor,
    LLMSettings,
    NarrativeAuthor,
    NarrativeDraft,
    NarrativeInput,
    NarrativeOrchestrator,
    OpenAINarrativeAuthor,
    PlayerIdentity,
    StoryStore,
)
from trpg_runtime.narrative.context import build_author_context
from trpg_runtime.narrative.storage import StoryConflict
from trpg_runtime.rules import RuleViolation
from trpg_runtime.story import load_bundle


class DraftAuthor(NarrativeAuthor):
    def __init__(self):
        self.calls = 0

    async def generate(self, *args, **kwargs):
        self.calls += 1
        return NarrativeDraft(narrative="The warden recognizes the letter and opens the gate.")


def setup_story(tmp_path: Path, author=None):
    bundle = load_bundle("examples/story/lantern_gate.yaml")
    store = StoryStore(tmp_path / "story.db")
    runtime = NarrativeOrchestrator(store, bundle, author or DraftAuthor())
    state = asyncio.run(
        runtime.start_session(PlayerIdentity(display_name="Ari"), session_id="test")
    )
    return bundle, store, runtime, state


def trust():
    return NarrativeInput(choice_id="trust", input_mode="choice")


def test_prose_only_author_gets_runtime_effects_and_idempotent_replay(tmp_path):
    author = DraftAuthor()
    _, store, runtime, state = setup_story(tmp_path, author)
    updated, first = asyncio.run(runtime.process_turn(state, trust(), request_id="first"))
    assert updated.variables["trust"] == 1
    assert updated.variables["last_choice"] == "trust"
    assert updated.current_beat_id == "courtyard"
    assert updated.revealed_fact_ids == {"gate-seal"}
    assert updated.version == updated.turn_number == 1
    events = store.story_events(state.session_id)
    replay, second = asyncio.run(runtime.process_turn(state, trust(), request_id="first"))
    assert (replay, second) == (updated, first)
    assert store.story_events(state.session_id) == events
    assert author.calls == 1


def test_stale_snapshot_is_rejected_before_another_provider_call(tmp_path):
    author = DraftAuthor()
    _, store, runtime, state = setup_story(tmp_path, author)
    updated, _ = asyncio.run(runtime.process_turn(state, trust(), request_id="first"))
    events = store.story_events(state.session_id)
    with pytest.raises(StoryConflict, match="stale state"):
        asyncio.run(runtime.process_turn(state, trust(), request_id="second"))
    assert author.calls == 1
    assert store.load_story_snapshot(state.session_id) == updated
    assert store.story_events(state.session_id) == events
    assert store.load_story_turn_result("second") is None


@pytest.mark.parametrize("same_request", [False, True])
def test_overlapping_generations_commit_once_across_store_instances(tmp_path, same_request):
    class BarrierAuthor(DraftAuthor):
        def __init__(self):
            super().__init__()
            self.ready = asyncio.Event()

        async def generate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                self.ready.set()
            await asyncio.wait_for(self.ready.wait(), timeout=5)
            return NarrativeDraft(narrative="One committed scene.")

    bundle, store, _, state = setup_story(tmp_path)

    async def run():
        author = BarrierAuthor()
        first = NarrativeOrchestrator(store, bundle, author)
        second = NarrativeOrchestrator(StoryStore(tmp_path / "story.db"), bundle, author)
        return await asyncio.gather(
            first.process_turn(state, trust(), request_id="first"),
            second.process_turn(state, trust(), request_id="first" if same_request else "second"),
            return_exceptions=True,
        )

    results = asyncio.run(run())
    assert sum(isinstance(item, StoryConflict) for item in results) == 1
    assert sum(isinstance(item, tuple) for item in results) == 1
    snapshot = store.load_story_snapshot(state.session_id)
    assert snapshot.version == snapshot.turn_number == 1
    assert snapshot.variables["trust"] == 1
    events = store.story_events(state.session_id)
    assert sum(event["type"] == "story_turn_completed" for event in events) == 1
    assert sum(event["type"] == "story_narrative_emitted" for event in events) == 1
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM story_turn_results").fetchone()[0] == 1


@pytest.mark.parametrize("mutation", ["duplicate", "omit", "references", "reveal"])
def test_legacy_author_cannot_change_the_resolved_transition(tmp_path, mutation):
    class MutatingAuthor(FakeNarrativeAuthor):
        async def generate(self, *args, **kwargs):
            proposal = await super().generate(*args, **kwargs)
            if mutation == "duplicate":
                proposal.state_patches.append(proposal.state_patches[0])
            elif mutation == "omit":
                proposal.state_patches.pop(0)
            elif mutation == "references":
                proposal.source_refs = ["invented:source"]
            else:
                proposal.revealed_fact_ids = []
            return proposal

    _, store, runtime, state = setup_story(tmp_path, MutatingAuthor())
    with pytest.raises(RuleViolation, match="runtime-resolved transition"):
        asyncio.run(runtime.process_turn(state, trust(), request_id="bad"))
    assert store.load_story_snapshot(state.session_id) == state
    assert store.load_story_turn_result("bad") is None
    assert [event["type"] for event in store.story_events(state.session_id)] == [
        "story_session_created",
        "story_turn_aborted",
    ]


def test_provider_receives_identity_consequences_and_public_history(tmp_path):
    bundle, _, _, state = setup_story(tmp_path)
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"narrative":"A complete scene."}'}}],
            },
        )

    async def run():
        author = OpenAINarrativeAuthor(
            LLMSettings("test", "https://example.invalid/v1", "unused", "offline"),
            transport=httpx.MockTransport(respond),
        )
        try:
            for name, trust_value, prior in [
                ("Ari", 1, "The warden helped you."),
                ("Bea", -10, "The warden betrayed you."),
            ]:
                variant = state.model_copy(
                    deep=True,
                    update={
                        "player_identity": PlayerIdentity(display_name=name, persona="A courier"),
                        "variables": {"trust": trust_value},
                        "last_narrative": prior,
                    },
                )
                draft = await author.generate(
                    bundle,
                    variant,
                    bundle.first_beat,
                    "I greet him.",
                    None,
                    [
                        {"type": "story_narrative_emitted", "payload": {"text": prior}},
                        {
                            "type": "story_author_proposal_accepted",
                            "payload": {"secret": "PRIVATE"},
                        },
                    ],
                )
                assert type(draft) is NarrativeDraft
        finally:
            await author.aclose()

    asyncio.run(run())
    contexts = [json.loads(call["messages"][1]["content"]) for call in captured]
    assert contexts[0] != contexts[1]
    assert contexts[1]["player"]["name"] == "Bea"
    assert contexts[1]["committed_state"]["variables"]["trust"] == -10
    assert contexts[1]["recent_public_history"][0]["text"] == "The warden betrayed you."
    assert "PRIVATE" not in json.dumps(contexts)
    assert bundle.fact("final-author-secret").content not in json.dumps(contexts)
    assert bundle.fact("missing-name").content not in json.dumps(contexts)
    assert contexts[0]["style"]["point_of_view"] == "second_person"


def test_context_only_exposes_authorized_reveals(tmp_path):
    bundle, _, _, state = setup_story(tmp_path)
    current = bundle.beat("archive")
    choice = current.choices[0]
    # An author-only fact must remain absent even if a malformed old snapshot lists it.
    state.revealed_fact_ids = {"final-author-secret"}
    context = build_author_context(
        bundle,
        state,
        current,
        bundle.beat(choice.next_beat_id),
        "Read it.",
        choice,
        [],
    )
    facts = {item["fact_id"] for item in context["visible_facts"]}
    assert facts == {"buried-oath"}
    assert context["resolved_choice"]["effects"][0]["value"] == 1


def test_child_writing_context_excludes_parent_future(tmp_path):
    class RecordingAuthor(DraftAuthor):
        async def generate(self, bundle, state, current, text, choice, recent):
            self.history = recent
            return await super().generate()

    bundle, store, runtime, state = setup_story(tmp_path)
    child = runtime.fork(state, "other")
    asyncio.run(runtime.process_turn(state, trust(), request_id="parent"))
    author = RecordingAuthor()
    asyncio.run(
        NarrativeOrchestrator(store, bundle, author).process_turn(
            child,
            NarrativeInput(text="Wait at the gate."),
            request_id="child",
        )
    )
    assert author.history == []


def test_failed_author_does_not_apply_resolved_effects(tmp_path):
    class FailingAuthor(DraftAuthor):
        async def generate(self, *args, **kwargs):
            raise RuntimeError("generation interrupted")

    _, store, runtime, state = setup_story(tmp_path, FailingAuthor())
    with pytest.raises(RuntimeError, match="generation interrupted"):
        asyncio.run(runtime.process_turn(state, trust(), request_id="failed"))
    assert store.load_story_snapshot(state.session_id) == state
    assert store.load_story_turn_result("failed") is None
