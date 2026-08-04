import asyncio
from types import SimpleNamespace

from trpg_runtime.agents import FakeAgentSuite
from trpg_runtime.gm_docs import (
    GMDocDeps,
    build_registry,
    gm_get_character_card,
    gm_search_rules,
    gm_search_world,
)
from trpg_runtime.runtime import TurnOrchestrator
from trpg_runtime.scenario import load_scenario
from trpg_runtime.storage import EventStore


def test_search_rules_finds_2d6_bands():
    state = load_scenario("examples/station_zero.yaml")
    deps = GMDocDeps(registry=build_registry(state), state=state)
    text = gm_search_rules(SimpleNamespace(deps=deps), "10 full success")
    assert "10 or higher" in text
    assert "[rules:pbta-core]" in text


def test_search_world_finds_public_and_hidden_facts():
    state = load_scenario("examples/station_zero.yaml")
    deps = GMDocDeps(registry=build_registry(state), state=state)
    text = gm_search_world(SimpleNamespace(deps=deps), "monitoring")
    assert "23:14" in text
    assert "Hidden fact" in text


def test_search_world_chinese_bigram():
    state = load_scenario("examples/station_zero.yaml", lang="zh")
    deps = GMDocDeps(registry=build_registry(state), state=state)
    text = gm_search_world(SimpleNamespace(deps=deps), "监控")
    assert "监控系统" in text


def test_character_card_tool_excludes_secrets():
    state = load_scenario("examples/station_zero.yaml")
    deps = GMDocDeps(registry=build_registry(state), state=state)
    text = gm_get_character_card(SimpleNamespace(deps=deps), "mira")
    assert "Mira" in text
    assert "dead camera feed" not in text


def test_character_card_tool_unknown_actor():
    state = load_scenario("examples/station_zero.yaml")
    deps = GMDocDeps(registry=build_registry(state), state=state)
    assert "Unknown actor" in gm_get_character_card(SimpleNamespace(deps=deps), "ghost")


def test_tool_calls_are_recorded_on_deps():
    state = load_scenario("examples/station_zero.yaml")
    deps = GMDocDeps(registry=build_registry(state), state=state)
    gm_search_rules(SimpleNamespace(deps=deps), "spotlight")
    gm_get_character_card(SimpleNamespace(deps=deps), "mira")
    assert [c["tool"] for c in deps.calls] == ["search_rules", "get_character_card"]
    assert deps.calls[0]["sources"] == ["rules:pbta-core"]


def test_runtime_records_tool_called_events(tmp_path):
    store = EventStore(tmp_path / "test.db")
    state = load_scenario("examples/station_zero.yaml", seed=42)
    store.append(state.campaign_id, 0, "campaign_created", {"seed": state.seed})
    store.save_snapshot(state)

    class ToolUsingSuite(FakeAgentSuite):
        def __init__(self):
            super().__init__()
            self.calls = [
                {"tool": "search_rules", "args": {"query": "2d6"}, "sources": ["rules:pbta-core"]},
                {
                    "tool": "search_world",
                    "args": {"query": "mira"},
                    "sources": ["world:control-room"],
                },
            ]

        def drain_tool_calls(self):
            calls, self.calls = self.calls[:1], self.calls[1:]
            return calls

    runtime = TurnOrchestrator(store, ToolUsingSuite())
    asyncio.run(runtime.process_turn(state, "I inspect the console."))
    tool_events = [e for e in store.events(state.campaign_id) if e["type"] == "tool_called"]
    assert len(tool_events) == 2  # once after plan, once after resolve
    assert tool_events[0]["payload"]["tool"] == "search_rules"
    assert tool_events[1]["payload"]["tool"] == "search_world"
