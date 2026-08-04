import asyncio

import pytest

from trpg_runtime.agents import FakeAgentSuite
from trpg_runtime.domain import SpotlightGrant, SpotlightOwner
from trpg_runtime.i18n import DEFAULT_LOCALE, UI_STRINGS
from trpg_runtime.projections import build_actor_view
from trpg_runtime.rules import SpotlightManager
from trpg_runtime.scenario import load_scenario


def test_load_scenario_zh():
    state = load_scenario("examples/station_zero.yaml", lang="zh")
    assert state.locale == "zh"
    assert "雨水" in state.opening
    assert "监控" in state.scene.public_facts[0]
    assert "故意篡改" in state.actors["mira"].knowledge[0].content


def test_load_scenario_default_locale():
    state = load_scenario("examples/station_zero.yaml")
    assert state.locale == "en"
    assert "Rain falls" in state.opening


def test_load_scenario_missing_locale_raises():
    with pytest.raises(ValueError, match="not available"):
        load_scenario("examples/station_zero.yaml", lang="ja")


def test_i18n_ui_key_parity():
    en_keys = set(UI_STRINGS[DEFAULT_LOCALE])
    for locale, table in UI_STRINGS.items():
        assert en_keys == set(table), f"UI keys diverge for locale {locale!r}"


def test_fake_agent_zh():
    state = load_scenario("examples/station_zero.yaml", lang="zh")
    state.spotlight = SpotlightManager.grant(
        SpotlightGrant(
            owner_type=SpotlightOwner.ACTOR, owner_id="mira", scopes={"own_action"}, reason="test"
        ),
        1,
    )
    view = build_actor_view(state, "mira", [], [])
    turn = asyncio.run(FakeAgentSuite(locale="zh").actor_turn(view))
    assert turn.speech == "别碰那一页。"
    assert "控制台" in turn.action
