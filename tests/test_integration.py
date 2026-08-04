import asyncio

from trpg_runtime.agents import FakeAgentSuite
from trpg_runtime.runtime import TurnOrchestrator
from trpg_runtime.scenario import load_scenario
from trpg_runtime.storage import EventStore


def test_fake_agent_full_turn(tmp_path):
    store = EventStore(tmp_path / "test.db")
    state = load_scenario("examples/station_zero.yaml", seed=42)
    store.append(state.campaign_id, 0, "campaign_created", {"seed": state.seed})
    store.save_snapshot(state)
    runtime = TurnOrchestrator(store, FakeAgentSuite())
    new_state, result = asyncio.run(runtime.process_turn(state, "I inspect the console log."))
    assert result.roll is not None
    assert result.actor_speech
    assert new_state.turn_number == 1
    assert new_state.spotlight.owner_id == state.player.player_id
    types = [e["type"] for e in store.events(state.campaign_id)]
    assert "dice_rolled" in types
    assert "audit_completed" in types
    assert types[-1] == "turn_completed"


def test_resume_snapshot(tmp_path):
    store = EventStore(tmp_path / "test.db")
    state = load_scenario("examples/station_zero.yaml")
    store.save_snapshot(state)
    loaded = store.load_snapshot(state.campaign_id)
    assert loaded == state
