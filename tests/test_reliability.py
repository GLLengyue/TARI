import asyncio

import pytest
from typer.testing import CliRunner

from trpg_runtime.agents import FakeAgentSuite
from trpg_runtime.cli import app
from trpg_runtime.domain import AuditResult, AuditViolation
from trpg_runtime.rules import RuleViolation
from trpg_runtime.runtime import TurnOrchestrator
from trpg_runtime.scenario import load_scenario
from trpg_runtime.storage import EventStore


def _fresh_store(tmp_path, name="test.db", seed=42):
    store = EventStore(tmp_path / name)
    state = load_scenario("examples/station_zero.yaml", seed=seed)
    store.append(state.campaign_id, 0, "campaign_created", {"seed": state.seed})
    store.save_snapshot(state)
    return store, state


class FailingResolveSuite(FakeAgentSuite):
    async def gm_resolve(self, view, player_input, roll):
        raise RuntimeError("gm exploded")


class RejectingAuditSuite(FakeAgentSuite):
    async def audit(self, view, turn):
        return AuditResult(
            accepted=False,
            violations=[AuditViolation(code="narrative_overreach", message="overreach")],
            retry_instruction="fix it",
        )


def test_failed_turn_commits_only_turn_aborted(tmp_path):
    store, state = _fresh_store(tmp_path)
    runtime = TurnOrchestrator(store, FailingResolveSuite())
    with pytest.raises(RuntimeError, match="gm exploded"):
        asyncio.run(runtime.process_turn(state, "I inspect the console."))
    events = store.events(state.campaign_id)
    assert [e["type"] for e in events] == ["campaign_created", "turn_aborted"]
    assert "gm exploded" in events[-1]["payload"]["error"]
    assert "dice_rolled" in events[-1]["payload"]["events"]  # rolled, then rolled back
    assert store.load_snapshot(state.campaign_id).turn_number == 0


def test_audit_rejection_rolls_back_partial_events(tmp_path):
    store, state = _fresh_store(tmp_path)
    runtime = TurnOrchestrator(store, RejectingAuditSuite())
    with pytest.raises(RuleViolation, match="audit rejected"):
        asyncio.run(runtime.process_turn(state, "I inspect the console."))
    types = [e["type"] for e in store.events(state.campaign_id)]
    assert types == ["campaign_created", "turn_aborted"]
    assert store.load_snapshot(state.campaign_id).turn_number == 0


def test_failed_turn_retry_gets_same_roll(tmp_path):
    store, state = _fresh_store(tmp_path)
    with pytest.raises(RuntimeError):
        asyncio.run(
            TurnOrchestrator(store, FailingResolveSuite()).process_turn(
                state, "I inspect the console."
            )
        )

    new_state, result = asyncio.run(
        TurnOrchestrator(store, FakeAgentSuite()).process_turn(state, "I inspect the console.")
    )
    control_store, control_state = _fresh_store(tmp_path, name="control.db")
    _, control_result = asyncio.run(
        TurnOrchestrator(control_store, FakeAgentSuite()).process_turn(
            control_state, "I inspect the console."
        )
    )
    assert new_state.turn_number == 1
    assert result.roll is not None and control_result.roll is not None
    assert result.roll.rolls == control_result.roll.rolls


def test_request_id_is_idempotent(tmp_path):
    store, state = _fresh_store(tmp_path)
    runtime = TurnOrchestrator(store, FakeAgentSuite())
    _, first = asyncio.run(
        runtime.process_turn(state, "I inspect the console.", request_id="req-1")
    )
    events_after_first = store.events(state.campaign_id)
    _, second = asyncio.run(
        runtime.process_turn(state, "I inspect the console.", request_id="req-1")
    )
    assert second == first
    assert store.events(state.campaign_id) == events_after_first
    assert store.load_snapshot(state.campaign_id).turn_number == 1


def test_distinct_request_ids_run_distinct_turns(tmp_path):
    store, state = _fresh_store(tmp_path)
    runtime = TurnOrchestrator(store, FakeAgentSuite())
    asyncio.run(runtime.process_turn(state, "I inspect the console.", request_id="req-1"))
    state = store.load_snapshot(state.campaign_id)
    asyncio.run(runtime.process_turn(state, "I move toward the door.", request_id="req-2"))
    assert store.load_snapshot(state.campaign_id).turn_number == 2


def test_recover_command_rebuilds_snapshot(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("TRPG_DB_PATH", str(db))
    store, state = _fresh_store(tmp_path, name="test.db", seed=184729)
    new_state, _ = asyncio.run(
        TurnOrchestrator(store, FakeAgentSuite()).process_turn(state, "I inspect the console.")
    )
    # Corrupt the snapshot, then recover from events.
    store.save_snapshot(load_scenario("examples/station_zero.yaml", seed=184729))
    runner = CliRunner()
    result = runner.invoke(app, ["recover", "station-zero", "examples/station_zero.yaml"])
    assert result.exit_code == 0, result.output
    rebuilt = store.load_snapshot("station-zero")
    assert rebuilt.turn_number == 1
    assert rebuilt.scene.public_facts == new_state.scene.public_facts
    assert rebuilt.version == new_state.version
    types = [e["type"] for e in store.events("station-zero")]
    assert "snapshot_rebuilt" in types
