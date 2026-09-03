import asyncio

import pytest

from trpg_runtime.narrative import (
    CanonPolicy,
    FakeNarrativeAuthor,
    NarrativeInput,
    NarrativeOrchestrator,
    NarrativeStatePatch,
    PlayerIdentity,
    StoryStore,
)
from trpg_runtime.rules import RuleViolation
from trpg_runtime.story import load_bundle


BUNDLE_PATH = "examples/story/lantern_gate.yaml"


def _runtime(tmp_path):
    bundle = load_bundle(BUNDLE_PATH)
    store = StoryStore(tmp_path / "story.db")
    runtime = NarrativeOrchestrator(store, bundle, FakeNarrativeAuthor())
    return bundle, store, runtime


def _start(tmp_path, session_id="lantern-test"):
    bundle, store, runtime = _runtime(tmp_path)
    state = asyncio.run(
        runtime.start_session(
            PlayerIdentity(
                display_name="Ari",
                identity_type="visitor",
                persona="A courier who refuses to leave a promise unfinished.",
            ),
            session_id=session_id,
            seed=7,
            canon_policy=CanonPolicy.GUIDED,
        )
    )
    return bundle, store, runtime, state


def test_story_bundle_loads_and_validates_references():
    bundle = load_bundle(BUNDLE_PATH)
    assert bundle.first_beat.beat_id == "arrival"
    assert bundle.beat("dawn").terminal
    assert bundle.fact("final-author-secret").visibility.value == "author_only"


def test_vertical_slice_completes_five_decisions_and_records_state(tmp_path):
    _, store, runtime, state = _start(tmp_path)
    assert state.last_narrative.startswith("At dusk")
    assert {choice.choice_id for choice in state.available_choices} == {"trust", "hide"}

    choices = ["trust", "follow", "read", "open"]
    for index, choice_id in enumerate(choices, start=1):
        state, result = asyncio.run(
            runtime.process_turn(
                state,
                NarrativeInput(text="", choice_id=choice_id, input_mode="choice"),
                request_id=f"slice-{index}",
            )
        )
        assert result.turn_number == index
        assert result.narrative
        assert state.version == index

    assert state.current_beat_id == "dawn"
    assert state.status == "completed"
    assert state.variables["trust"] == 4
    assert "gate-seal" in state.revealed_fact_ids
    assert "buried-oath" in state.revealed_fact_ids

    event_types = [event["type"] for event in store.story_events(state.session_id)]
    assert event_types[0] == "story_session_created"
    assert event_types.count("story_narrative_emitted") == 4
    assert event_types[-1] == "story_turn_completed"
    assert store.load_story_snapshot(state.session_id).status == "completed"


def test_request_id_is_idempotent_for_story_turns(tmp_path):
    _, store, runtime, state = _start(tmp_path)
    first_state, first = asyncio.run(
        runtime.process_turn(
            state,
            NarrativeInput(choice_id="trust", input_mode="choice"),
            request_id="same-story-request",
        )
    )
    events_after_first = store.story_events(state.session_id)
    second_state, second = asyncio.run(
        runtime.process_turn(
            state,
            NarrativeInput(choice_id="trust", input_mode="choice"),
            request_id="same-story-request",
        )
    )
    assert second == first
    assert second_state == first_state
    assert store.story_events(state.session_id) == events_after_first


def test_invalid_choice_aborts_without_committing_partial_turn(tmp_path):
    _, store, runtime, state = _start(tmp_path)
    with pytest.raises(RuleViolation, match="choice is not available"):
        asyncio.run(
            runtime.process_turn(
                state,
                NarrativeInput(choice_id="invented", input_mode="choice"),
                request_id="bad-choice",
            )
        )

    assert store.load_story_snapshot(state.session_id).turn_number == 0
    events = store.story_events(state.session_id)
    assert [event["type"] for event in events] == [
        "story_session_created",
        "story_turn_aborted",
    ]
    assert "story_player_input_received" not in [event["type"] for event in events]


def test_branch_keeps_parent_and_child_timelines_independent(tmp_path):
    _, store, runtime, state = _start(tmp_path)
    child = runtime.fork(state, "hesitation")

    main_state, _ = asyncio.run(
        runtime.process_turn(
            state,
            NarrativeInput(choice_id="trust", input_mode="choice"),
            request_id="main-choice",
        )
    )
    child_state, _ = asyncio.run(
        runtime.process_turn(
            child,
            NarrativeInput(choice_id="hide", input_mode="choice"),
            request_id="child-choice",
        )
    )

    assert main_state.branch_id == "main"
    assert child_state.branch_id == "hesitation"
    assert main_state.variables["trust"] == 1
    assert child_state.variables["trust"] == -1
    assert store.load_story_snapshot(state.session_id, "main").variables["trust"] == 1
    assert store.load_story_snapshot(state.session_id, "hesitation").variables["trust"] == -1

    child_events = store.story_events(state.session_id, "hesitation")
    assert child_events[0]["type"] == "story_session_created"
    assert any(event["type"] == "story_branch_created" for event in child_events)
    assert any(event["type"] == "story_narrative_emitted" for event in child_events)
    assert {item["branch_id"] for item in store.list_story_branches(state.session_id)} == {
        "main",
        "hesitation",
    }


def test_author_cannot_write_outside_story_state_surface(tmp_path):
    bundle, store, _, state = _start(tmp_path)

    class BadAuthor(FakeNarrativeAuthor):
        async def generate(self, *args, **kwargs):
            proposal = await super().generate(*args, **kwargs)
            proposal.state_patches[0].path = "current_beat_id"
            return proposal

    runtime = NarrativeOrchestrator(store, bundle, BadAuthor())
    with pytest.raises(RuleViolation, match="forbidden narrative patch path"):
        asyncio.run(
            runtime.process_turn(
                state,
                NarrativeInput(choice_id="trust", input_mode="choice"),
                request_id="bad-patch",
            )
        )
    assert store.load_story_snapshot(state.session_id).turn_number == 0
    assert store.story_events(state.session_id)[-1]["type"] == "story_turn_aborted"


def test_freeform_input_does_not_silently_advance_beat(tmp_path):
    _, _, runtime, state = _start(tmp_path)
    new_state, result = asyncio.run(
        runtime.process_turn(
            state,
            NarrativeInput(text="I ask who is waiting.", input_mode="freeform"),
            request_id="freeform-1",
        )
    )
    assert new_state.current_beat_id == "arrival"
    assert new_state.completed_beat_ids == []
    assert result.choices
    assert new_state.variables["last_input"] == "I ask who is waiting."


def test_mapping_input_and_request_id_is_session_scoped(tmp_path):
    _, store, runtime, first = _start(tmp_path, session_id="first-session")
    second = asyncio.run(
        runtime.start_session(
            PlayerIdentity(display_name="Bea"),
            session_id="second-session",
        )
    )

    first_state, result = asyncio.run(
        runtime.process_turn(
            first,
            {"choice_id": "trust", "input_mode": "choice"},
            request_id="shared-request",
        )
    )
    assert first_state.current_beat_id == "courtyard"
    assert result.choice_id == "trust"

    with pytest.raises(RuleViolation, match="different story session"):
        asyncio.run(
            runtime.process_turn(
                second,
                {"choice_id": "trust", "input_mode": "choice"},
                request_id="shared-request",
            )
        )
    assert store.load_story_snapshot("second-session").turn_number == 0


def test_author_cannot_invent_an_undeclared_state_effect(tmp_path):
    bundle, store, _, state = _start(tmp_path)

    class BadEffectAuthor(FakeNarrativeAuthor):
        async def generate(self, *args, **kwargs):
            proposal = await super().generate(*args, **kwargs)
            proposal.state_patches.append(
                NarrativeStatePatch(
                    operation="set",
                    path="variables.cheat",
                    new_value=True,
                    reason="invented effect",
                )
            )
            return proposal

    runtime = NarrativeOrchestrator(store, bundle, BadEffectAuthor())
    with pytest.raises(RuleViolation, match="undeclared state effect"):
        asyncio.run(
            runtime.process_turn(
                state,
                NarrativeInput(choice_id="trust", input_mode="choice"),
                request_id="bad-effect",
            )
        )
    assert store.load_story_snapshot(state.session_id).turn_number == 0
    assert store.story_events(state.session_id)[-1]["type"] == "story_turn_aborted"
