from trpg_runtime.domain import KnowledgeItem, SpotlightGrant, SpotlightOwner
from trpg_runtime.projections import build_actor_view, build_gm_view, compact_gm_view
from trpg_runtime.rules import SpotlightManager
from trpg_runtime.scenario import load_scenario


def test_actor_view_excludes_scene_hidden_facts_and_story_framework():
    state = load_scenario("examples/station_zero.yaml")
    state.spotlight = SpotlightManager.grant(
        SpotlightGrant(
            owner_type=SpotlightOwner.ACTOR, owner_id="mira", scopes={"own_action"}, reason="test"
        ),
        1,
    )
    view = build_actor_view(state, "mira", [], [])
    serialized = view.model_dump_json()
    assert "final maintenance-log page" not in serialized
    assert "saboteur's identity" not in serialized
    assert "deliberately tampered" in serialized  # Mira genuinely knows this fact.


def test_actor_view_excludes_other_actors_knowledge_and_secrets():
    state = load_scenario("examples/station_zero.yaml")
    state.actors["kael"] = state.actors["mira"].model_copy(
        update={
            "actor_id": "kael",
            "name": "Kael",
            "knowledge": [
                KnowledgeItem(
                    fact_id="kael_secret",
                    content="Kael is the saboteur.",
                    confidence=1.0,
                    source="test",
                )
            ],
            "secrets": ["Kael planted the device."],
        }
    )
    state.spotlight = SpotlightManager.grant(
        SpotlightGrant(
            owner_type=SpotlightOwner.ACTOR, owner_id="mira", scopes={"own_action"}, reason="test"
        ),
        1,
    )
    view = build_actor_view(state, "mira", [], [])
    serialized = view.model_dump_json()
    assert "kael_secret" not in serialized
    assert "Kael is the saboteur" not in serialized
    assert "Kael planted the device" not in serialized
    assert "Mira inspected the dead camera feed" in serialized  # own secrets stay visible to self


def test_actor_view_shape_is_fiction_only():
    state = load_scenario("examples/station_zero.yaml")
    state.spotlight = SpotlightManager.grant(
        SpotlightGrant(
            owner_type=SpotlightOwner.ACTOR, owner_id="mira", scopes={"own_action"}, reason="test"
        ),
        1,
    )
    view = build_actor_view(
        state, "mira", ["The player asked about the log."], ["GM said something."]
    )
    data = view.model_dump(mode="json")
    assert set(data) == {
        "campaign_id",
        "turn_number",
        "actor",
        "public_facts",
        "observations",
        "recent_public_events",
        "spotlight",
    }
    assert data["actor"]["actor_id"] == "mira"
    assert data["observations"] == ["The player asked about the log."]
    assert data["recent_public_events"] == ["GM said something."]


def test_compact_gm_view_drops_presentation_blobs_but_keeps_adjudication_fields():
    state = load_scenario("examples/station_zero.yaml")
    state.opening = "<div>long HTML greeting</div>"
    state.actors["mira"] = state.actors["mira"].model_copy(
        update={"attributes": {"card": {"first_mes": "<div>HTML</div>"}}}
    )
    view = build_gm_view(state, ["recent beat"])
    serialized = compact_gm_view(view)
    assert "<div>long HTML greeting</div>" not in serialized
    assert "<div>HTML</div>" not in serialized
    assert "recent beat" in serialized
    assert state.scene.public_facts[0] in serialized
    assert '"attributes"' not in serialized
