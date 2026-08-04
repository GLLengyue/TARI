from trpg_runtime.agents import PydanticAISuite
from trpg_runtime.config import load_runtime_config


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
