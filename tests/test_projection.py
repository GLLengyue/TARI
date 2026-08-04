from trpg_runtime.projections import build_actor_view
from trpg_runtime.rules import SpotlightManager
from trpg_runtime.domain import SpotlightGrant, SpotlightOwner
from trpg_runtime.scenario import load_scenario


def test_actor_view_excludes_scene_hidden_facts_and_story_framework():
    state = load_scenario("examples/station_zero.yaml")
    state.spotlight = SpotlightManager.grant(
        SpotlightGrant(owner_type=SpotlightOwner.ACTOR, owner_id="mira", scopes={"own_action"}, reason="test"), 1
    )
    view = build_actor_view(state, "mira", [], [])
    serialized = view.model_dump_json()
    assert "final maintenance-log page" not in serialized
    assert "saboteur's identity" not in serialized
    assert "deliberately tampered" in serialized  # Mira genuinely knows this fact.
