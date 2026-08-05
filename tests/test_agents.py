import asyncio

from trpg_runtime.agents import FakeAgentSuite, PydanticAISuite
from trpg_runtime.config import load_runtime_config
from trpg_runtime.runtime import TurnOrchestrator
from trpg_runtime.scenario import load_scenario
from trpg_runtime.storage import EventStore


def test_pydantic_ai_suite_registers_gm_tools(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    suite = PydanticAISuite(load_runtime_config("config/agents.yaml"))
    tool_names = set(suite.gm._function_toolset.tools)
    assert {
        "gm_search_rules",
        "gm_search_world",
        "gm_get_character_card",
        "gm_get_scenario_outline",
    } <= tool_names


def test_gm_wrap_emits_scene_continuation_after_actor(tmp_path):
    state = load_scenario("examples/station_zero.yaml", seed=42)
    store = EventStore(tmp_path / "test.db")
    store.append(state.campaign_id, 0, "campaign_created", {"seed": state.seed})
    store.save_snapshot(state)

    class WrapSuite(FakeAgentSuite):
        async def gm_wrap(self, view, player_input, roll, resolution, performance):
            return "A streetlamp flickers as Mira speaks."

    async def run():
        runtime = TurnOrchestrator(store, WrapSuite("en"))
        new_state, result = await runtime.process_turn(
            state, "Ask Mira about the log", request_id="wrap-1"
        )
        assert result.gm_wrap_narration == "A streetlamp flickers as Mira speaks."
        events = store.events(state.campaign_id)
        actor_idx = next(
            i
            for i, e in enumerate(events)
            if e["type"] == "public_narrative_emitted" and e["payload"]["speaker"] == "mira"
        )
        wrap_idx = next(
            i
            for i, e in enumerate(events)
            if e["type"] == "public_narrative_emitted"
            and e["payload"]["speaker"] == "gm"
            and "streetlamp" in e["payload"]["text"]
        )
        assert wrap_idx > actor_idx
        assert new_state.turn_number == 1

    asyncio.run(run())
