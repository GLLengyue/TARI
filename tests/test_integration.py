import asyncio

from trpg_runtime.agents import FakeAgentSuite
from trpg_runtime.domain import SpotlightGrant, SpotlightOwner
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


def test_invalid_spotlight_proposal_falls_back_without_failing_turn(tmp_path):
    store = EventStore(tmp_path / "test.db")
    state = load_scenario("examples/station_zero.yaml", seed=42)
    store.append(state.campaign_id, 0, "campaign_created", {"seed": state.seed})
    store.save_snapshot(state)

    class WaywardGMSuite(FakeAgentSuite):
        async def gm_resolve(self, view, player_input, roll):
            decision = await super().gm_resolve(view, player_input, roll)
            decision.next_spotlight = SpotlightGrant(
                owner_type=SpotlightOwner.ACTOR,
                owner_id="ghost",
                scopes={"own_action"},
                reason="bad proposal",
            )
            return decision

    runtime = TurnOrchestrator(store, WaywardGMSuite())
    new_state, result = asyncio.run(runtime.process_turn(state, "I inspect the console."))
    assert new_state.turn_number == 1
    assert new_state.spotlight.owner_id == state.player.player_id
    types = [e["type"] for e in store.events(state.campaign_id)]
    assert "spotlight_policy_fallback" in types
    assert types[-1] == "turn_completed"


def test_progress_stages_emitted_in_order(tmp_path):
    store = EventStore(tmp_path / "test.db")
    state = load_scenario("examples/station_zero.yaml", seed=42)
    store.append(state.campaign_id, 0, "campaign_created", {"seed": state.seed})
    store.save_snapshot(state)
    stages = []
    runtime = TurnOrchestrator(
        store, FakeAgentSuite(), on_progress=lambda stage, payload: stages.append(stage)
    )
    asyncio.run(runtime.process_turn(state, "I inspect the console log."))
    assert stages[0] == "player_action"
    assert stages[1] == "gm_planning"
    assert stages.index("rolling") < stages.index("dice") < stages.index("gm_resolving")
    assert stages.index("gm_resolving") < stages.index("committing") < stages.index("actor_turn")
    assert stages.index("actor_turn") < stages.index("auditing") < stages.index("completed")
