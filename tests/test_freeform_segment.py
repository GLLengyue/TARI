"""Freeform segments, and the append-only prefix that keeps the cache warm.

Two contracts matter most here:

* a refused action writes prose and changes **no** state;
* the prompt prefix never moves, so the provider cache keeps hitting.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trpg_runtime.cli import app
from trpg_runtime.narrative import (
    FakeNarrativeAuthor,
    NarrativeOrchestrator,
    PlayerIdentity,
    StoryStore,
)
from trpg_runtime.narrative.session_context import (
    build_messages,
    build_static_prefix,
    prefix_fingerprint,
    project_events,
    render_state_snapshot,
)
from trpg_runtime.rules import RuleViolation
from trpg_runtime.story import load_bundle

SAMPLE = Path("examples/story/last_ferry.yaml")


class RecordingAuthor(FakeNarrativeAuthor):
    """Fake author that keeps every request so prefix stability is assertable."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rule_requests: list[list[dict[str, str]]] = []
        self.narrate_requests: list[list[dict[str, str]]] = []

    async def rule_action(self, messages, action):
        self.rule_requests.append([dict(message) for message in messages])
        return await super().rule_action(messages, action)

    async def narrate_action(self, messages, action, ruling):
        self.narrate_requests.append([dict(message) for message in messages])
        return await super().narrate_action(messages, action, ruling)


def setup(tmp_path, author=None):
    bundle = load_bundle(SAMPLE)
    store = StoryStore(tmp_path / "freeform.db")
    runtime = NarrativeOrchestrator(store, bundle, author or FakeNarrativeAuthor())
    state = asyncio.run(
        runtime.start_session(PlayerIdentity(display_name="林岑"), session_id="ferry")
    )
    return bundle, store, runtime, state


def act(runtime, state, text, request_id):
    return asyncio.run(runtime.process_freeform_action(state, text, request_id))


# --- prompt prefix: the cache contract -------------------------------------


def test_static_prefix_is_deterministic(tmp_path):
    bundle, _, _, state = setup(tmp_path)
    assert prefix_fingerprint(build_static_prefix(bundle, state)) == prefix_fingerprint(
        build_static_prefix(bundle, state)
    )


def test_prefix_survives_opening_and_acting(tmp_path):
    """Nothing in the prefix may move as the story advances."""
    bundle, _, runtime, state = setup(tmp_path)
    before = prefix_fingerprint(build_static_prefix(bundle, state))
    state = runtime.open_segment(state, tension="门锁着", stakes="孩子在外面淋雨")
    state, _ = act(runtime, state, "我砸开门", "a1")
    state, _ = act(runtime, state, "我扔出毯子", "a2")
    assert prefix_fingerprint(build_static_prefix(bundle, state)) == before


def test_projection_prefix_is_stable():
    events = [
        {"type": "story_player_input_received", "payload": {"text": "一", "automatic": False}},
        {"type": "story_narrative_emitted", "payload": {"text": "回一"}},
        {"type": "story_player_input_received", "payload": {"text": "二", "automatic": False}},
        {"type": "story_narrative_emitted", "payload": {"text": "回二"}},
    ]
    for cut in range(len(events) + 1):
        assert project_events(events)[: len(project_events(events[:cut]))] == project_events(
            events[:cut]
        )


def test_narration_request_extends_the_ruling_request(tmp_path):
    """The narration call must only append to the ruling call's messages.

    If it rebuilt them, every pair of calls would invalidate the cache.
    """
    author = RecordingAuthor()
    _, _, runtime, state = setup(tmp_path, author=author)
    state = runtime.open_segment(state, tension="门锁着")
    act(runtime, state, "我砸开门", "a1")

    ruling_messages = author.rule_requests[0]
    narration_messages = author.narrate_requests[0]
    assert narration_messages[: len(ruling_messages)] == ruling_messages


def test_consecutive_turns_share_the_static_prefix(tmp_path):
    author = RecordingAuthor()
    bundle, _, runtime, state = setup(tmp_path, author=author)
    prefix = build_static_prefix(bundle, state)
    state = runtime.open_segment(state, tension="门锁着")
    state, _ = act(runtime, state, "我砸开门", "a1")
    act(runtime, state, "我扔出毯子", "a2")

    for request in (author.rule_requests[0], author.rule_requests[1]):
        assert request[: len(prefix)] == prefix


def test_build_messages_puts_history_between_prefix_and_turn(tmp_path):
    bundle, _, _, state = setup(tmp_path)
    prefix = build_static_prefix(bundle, state)
    events = [{"type": "story_narrative_emitted", "payload": {"text": "开场"}}]
    messages = build_messages(prefix, events, [{"role": "user", "content": "我做点什么"}])
    assert messages[: len(prefix)] == prefix
    assert messages[-1] == {"role": "user", "content": "我做点什么"}


# --- segment lifecycle ------------------------------------------------------


def test_open_segment_requires_a_tension(tmp_path):
    _, _, runtime, state = setup(tmp_path)
    with pytest.raises(RuleViolation):
        runtime.open_segment(state, tension="   ")


def test_open_segment_twice_is_rejected(tmp_path):
    _, _, runtime, state = setup(tmp_path)
    state = runtime.open_segment(state, tension="门锁着")
    with pytest.raises(RuleViolation):
        runtime.open_segment(state, tension="另一个张力")


def test_action_requires_an_open_segment(tmp_path):
    _, _, runtime, state = setup(tmp_path)
    with pytest.raises(RuleViolation):
        act(runtime, state, "我做点什么", "a1")


def test_close_segment_restores_choices(tmp_path):
    _, _, runtime, state = setup(tmp_path)
    state = runtime.open_segment(state, tension="门锁着")
    assert not state.available_choices
    state = runtime.close_segment(state, resolution="玩家离开了")
    assert state.active_segment is None
    assert state.available_choices


# --- acting -----------------------------------------------------------------


def test_feasible_action_changes_state(tmp_path):
    _, _, runtime, state = setup(tmp_path)
    state = runtime.open_segment(state, tension="门锁着")
    state, result = act(runtime, state, "我砸开门", "a1")
    assert result.debug["feasible"] is True
    assert state.variables["last_action"] == "我砸开门"
    assert state.turn_number == 2


def test_refused_action_writes_prose_and_no_state(tmp_path):
    """The core invariant: the world answers, but nothing actually changes."""
    _, _, runtime, state = setup(tmp_path)
    state = runtime.open_segment(state, tension="门锁着")
    before = dict(state.variables)
    state, result = act(runtime, state, "我掏出手枪", "a1")
    assert result.debug["feasible"] is False
    assert result.narrative.strip()
    assert state.variables == before
    assert state.turn_number == 2


def test_segment_stays_open_and_accumulates(tmp_path):
    _, _, runtime, state = setup(tmp_path)
    state = runtime.open_segment(state, tension="门锁着")
    for index in range(3):
        state, _ = act(runtime, state, f"第{index}步", f"a{index}")
    assert state.active_segment is not None
    assert len(state.active_segment.actions) == 3
    assert state.active_segment.status == "open"


def test_resolution_closes_the_segment(tmp_path):
    author = FakeNarrativeAuthor(resolve_on="放弃")
    _, _, runtime, state = setup(tmp_path, author=author)
    state = runtime.open_segment(state, tension="走还是留")
    state, result = act(runtime, state, "我决定放弃送信", "a1")
    assert result.debug["tension_resolved"] is True
    assert state.active_segment is None
    assert state.available_choices


def test_ruling_is_journaled_for_replay(tmp_path):
    """The ruling must land in the log: replay reads it instead of re-ruling."""
    _, store, runtime, state = setup(tmp_path)
    state = runtime.open_segment(state, tension="门锁着")
    act(runtime, state, "我掏出手枪", "a1")
    events = store.story_events("ferry", "main")
    ruled = [event for event in events if event["type"] == "story_action_ruled"]
    assert len(ruled) == 1
    assert ruled[0]["payload"]["feasible"] is False
    assert ruled[0]["payload"]["reason"]


def test_replaying_a_request_id_does_not_re_rule(tmp_path):
    author = RecordingAuthor()
    _, _, runtime, state = setup(tmp_path, author=author)
    state = runtime.open_segment(state, tension="门锁着")
    state, first = act(runtime, state, "我砸开门", "a1")
    _, second = act(runtime, state, "我砸开门", "a1")
    assert second.narrative == first.narrative
    assert len(author.rule_requests) == 1


def test_stale_state_is_rejected(tmp_path):
    from trpg_runtime.narrative.storage import StoryConflict

    _, _, runtime, state = setup(tmp_path)
    state = runtime.open_segment(state, tension="门锁着")
    stale = state.model_copy(deep=True)
    act(runtime, state, "我砸开门", "a1")
    with pytest.raises(StoryConflict):
        act(runtime, stale, "我再来一次", "a2")


# --- state snapshots: the model reads the ledger, never recalls -------------


def test_snapshot_drops_bookkeeping_keys():
    payload = {"variables": {"trust": 1, "last_input": "继续阅读", "last_choice": "x"}}
    rendered = render_state_snapshot(payload)
    assert "trust=1" in rendered
    assert "last_input" not in rendered
    assert "last_choice" not in rendered


def test_snapshot_rendering_is_deterministic():
    payload = {"variables": {"b": 2, "a": 1}, "relationship_values": {"r": 3}}
    assert render_state_snapshot(payload) == render_state_snapshot(
        dict(reversed(list(payload.items())))
    )


def test_snapshot_is_journaled_and_projected(tmp_path):
    """The runtime writes the snapshot; the model reads it next turn."""
    author = RecordingAuthor()
    _, store, runtime, state = setup(tmp_path, author=author)
    state = runtime.open_segment(state, tension="门锁着")
    state, _ = act(runtime, state, "我砸开门", "a1")

    events = store.story_events("ferry", "main")
    snapshots = [event for event in events if event["type"] == "story_state_snapshot"]
    assert len(snapshots) == 2  # one from open_segment, one from the action

    rendered = render_state_snapshot(snapshots[-1]["payload"])
    assert rendered.startswith("（旁白）当前状态：")
    assert "last_action=我砸开门" in rendered

    projected = project_events(events)
    assert rendered in [message["content"] for message in projected]
    # the template forbids system after the first message; snapshots are user
    assert all(message["role"] != "system" for message in projected[1:])


def test_later_snapshot_reflects_the_latest_state(tmp_path):
    """The model must read the newest value, not re-add deltas itself."""
    _, store, runtime, state = setup(tmp_path)
    state = runtime.open_segment(state, tension="门锁着")
    state, _ = act(runtime, state, "第一步", "a1")
    state, _ = act(runtime, state, "第二步", "a2")

    events = store.story_events("ferry", "main")
    snapshots = [event for event in events if event["type"] == "story_state_snapshot"]
    rendered = render_state_snapshot(snapshots[-1]["payload"])
    assert "last_action=第二步" in rendered

    projected = project_events(events)
    contents = [
        message["content"] for message in projected if "当前状态" in str(message.get("content"))
    ]
    assert "last_action=第二步" in contents[-1]


# --- CLI --------------------------------------------------------------------


def test_cli_story_act_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_DB_PATH", str(tmp_path / "cli.db"))
    runner = CliRunner()
    created = runner.invoke(
        app,
        ["story-new", str(SAMPLE), "--session-id", "cli-demo", "--player-name", "林岑"],
    )
    assert created.exit_code == 0, created.output

    first = runner.invoke(
        app,
        ["story-act", str(SAMPLE), "cli-demo", "我砸开门", "--request-id", "c1"],
    )
    assert first.exit_code == 0, first.output
    assert "进入自由段" in first.output

    refused = runner.invoke(
        app,
        ["story-act", str(SAMPLE), "cli-demo", "我掏出手枪", "--request-id", "c2"],
    )
    assert refused.exit_code == 0, refused.output
    assert "状态未发生任何变化" in refused.output


def test_cli_story_play_is_one_command(tmp_path, monkeypatch):
    """The whole loop in one command: /read, type freely, /done, quit."""
    monkeypatch.setenv("TRPG_DB_PATH", str(tmp_path / "cli.db"))
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "story-play",
            str(SAMPLE),
            "repl-demo",
            "--player-name",
            "林岑",
        ],
        input="/read 2\n我砸开储物间的门\n/done\n/quit\n",
    )
    assert result.exit_code == 0, result.output
    assert "新会话已创建" in result.output
    assert "Scene 1" in result.output
    assert "轮到你了" in result.output
    assert "Turn" in result.output
    assert "自由段结束" in result.output

    # the freeform action really landed in the world
    import json
    import sqlite3

    con = sqlite3.connect(tmp_path / "cli.db")
    state = json.loads(
        con.execute(
            "SELECT state_json FROM story_snapshots WHERE session_id='repl-demo' AND branch_id='main'"
        ).fetchone()[0]
    )
    assert state["variables"]["last_action"] == "我砸开储物间的门"
    assert state["active_segment"] is None
