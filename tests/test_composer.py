import pytest

from trpg_runtime.composer import ComposeRequest, build_preview, compose_state
from trpg_runtime.resource_library import ResourceLibrary


def _lib() -> ResourceLibrary:
    lib = ResourceLibrary()
    lib.scan()
    return lib


def test_card_only_compose_uses_card_actor_and_requested_locale():
    lib = _lib()
    card_id = next(r.id for r in lib.by_kind("cards") if "mobi" in r.id)
    state = compose_state(lib, ComposeRequest(card_id=card_id, lang="zh"))
    assert state.locale == "zh"
    assert "墨笔" in state.actors


def test_scenario_plus_card_replaces_actor_only():
    lib = _lib()
    scenario_id = next(r.id for r in lib.by_kind("scenarios") if "station_zero" in r.id)
    card_id = next(r.id for r in lib.by_kind("cards") if "mobi" in r.id)
    state = compose_state(
        lib, ComposeRequest(scenario_id=scenario_id, card_id=card_id, lang="en")
    )
    assert state.scene.scene_id == "control-room"
    assert "墨笔" in state.actors
    assert "mira" not in state.actors
    assert "monitoring" in " ".join(state.scene.public_facts)


def test_world_info_merges_into_public_facts():
    lib = _lib()
    world_id = next(r.id for r in lib.by_kind("worlds") if "wangushi" in r.id)
    state = compose_state(lib, ComposeRequest(world_id=world_id, lang="zh"))
    assert any("摊主记得所有客人" in f for f in state.scene.public_facts)


def test_lang_mismatch_raises():
    lib = _lib()
    scenario_id = next(r.id for r in lib.by_kind("scenarios") if "station_zero" in r.id)
    with pytest.raises(ValueError, match="locale"):
        compose_state(lib, ComposeRequest(scenario_id=scenario_id, lang="fr"))


def test_unknown_ruleset_falls_back_to_default_in_preview():
    lib = _lib()
    state = compose_state(
        lib, ComposeRequest(ruleset_id="does-not-exist", lang="en")
    )
    assert state.rules.ruleset_id == "does-not-exist"
    preview = build_preview(lib, ComposeRequest(ruleset_id="does-not-exist", lang="en"))
    assert "2d6" in preview["rules"]["effective_text"]


def test_custom_rules_replace_preset():
    lib = _lib()
    preview = build_preview(
        lib,
        ComposeRequest(ruleset_id="pbta-minimal", custom_rules="GM decides everything.", lang="en"),
    )
    assert preview["rules"]["effective_text"] == "GM decides everything."


def test_wangu_xiaoqian_scenario_composes_with_card_and_world():
    lib = _lib()
    scenario_id = next(
        r.id for r in lib.by_kind("scenarios") if "wangu_xiaoqian" in r.id
    )
    card_id = next(
        r.id for r in lib.by_kind("cards") if "nie-xiaoqian" in r.id
    )
    world_id = next(r.id for r in lib.by_kind("worlds") if "wangushi" in r.id)
    state = compose_state(
        lib,
        ComposeRequest(
            scenario_id=scenario_id,
            card_id=card_id,
            world_id=world_id,
            lang="zh",
            campaign_id="wangu_xiaoqian",
            seed=20260805,
        ),
    )
    assert state.title == "晚孤市·倩影灯花"
    assert "聂小倩" in state.actors
    assert len(state.story_framework.required_beats) == 5
    assert len(state.scene.public_facts) >= 8
    assert len(state.scene.hidden_facts) == 4
