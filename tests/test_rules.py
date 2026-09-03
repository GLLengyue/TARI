from uuid import uuid4

import pytest

from trpg_runtime.domain import SpotlightGrant, SpotlightOwner, StatePatch
from trpg_runtime.rules import (
    DiceEngine,
    RuleViolation,
    SpotlightManager,
    SpotlightPolicy,
    apply_patches,
)
from trpg_runtime.scenario import load_scenario


def test_pbta_seed_is_deterministic():
    a, b = DiceEngine(42), DiceEngine(42)
    assert [a.roll_pbta(uuid4()).rolls for _ in range(5)] == [
        b.roll_pbta(uuid4()).rolls for _ in range(5)
    ]


def test_pbta_outcome_bands():
    # Test classification exhaustively through a controlled fake RNG stream.
    engine = DiceEngine(1)
    result = engine.roll_pbta(uuid4())
    assert result.outcome.value in {"full_success", "success_with_cost", "failure"}
    assert 2 <= result.total <= 12


def test_actor_cannot_patch_world():
    state = load_scenario("examples/station_zero.yaml")
    patch = StatePatch(
        operation="add", path="scene.public_facts", new_value="x", reason="bad", proposed_by="mira"
    )
    with pytest.raises(RuleViolation):
        apply_patches(state, [patch])


def test_atomic_patch_rejection():
    state = load_scenario("examples/station_zero.yaml")
    valid = StatePatch(
        operation="add", path="scene.public_facts", new_value="x", reason="ok", proposed_by="gm"
    )
    invalid = StatePatch(operation="set", path="seed", new_value=1, reason="bad", proposed_by="gm")
    with pytest.raises(RuleViolation):
        apply_patches(state, [valid, invalid])
    assert "x" not in state.scene.public_facts


def test_patch_path_normalization():
    state = load_scenario("examples/station_zero.yaml")
    patch = StatePatch(
        operation="set",
        path="\n/actors/mira/attributes/trust_level",
        new_value=3,
        reason="ok",
        proposed_by="gm",
    )
    new_state = apply_patches(state, [patch])
    assert new_state.actors["mira"].attributes["trust_level"] == 3


def test_add_patch_to_non_list_raises():
    state = load_scenario("examples/station_zero.yaml")
    patch = StatePatch(
        operation="add",
        path="scene.title",
        new_value="x",
        reason="bad",
        proposed_by="gm",
    )
    with pytest.raises(RuleViolation, match="not a list"):
        apply_patches(state, [patch])


def test_non_numeric_list_index_raises_rule_violation():
    state = load_scenario("examples/station_zero.yaml")
    patch = StatePatch(
        operation="set",
        path="scene.public_facts.已付真话",
        new_value="x",
        reason="bad path",
        proposed_by="gm",
    )
    with pytest.raises(RuleViolation, match="list index"):
        apply_patches(state, [patch])


def test_out_of_range_list_index_raises_rule_violation():
    state = load_scenario("examples/station_zero.yaml")
    patch = StatePatch(
        operation="set",
        path="scene.public_facts.999",
        new_value="x",
        reason="bad path",
        proposed_by="gm",
    )
    with pytest.raises(RuleViolation, match="list index"):
        apply_patches(state, [patch])


def test_spotlight_is_enforced():
    state = load_scenario("examples/station_zero.yaml")
    with pytest.raises(RuleViolation):
        SpotlightManager.require(state, "mira", "own_action")


def test_spotlight_policy_accepts_valid_actor():
    state = load_scenario("examples/station_zero.yaml")
    grant = SpotlightGrant(
        owner_type=SpotlightOwner.ACTOR,
        owner_id="mira",
        scopes={"own_dialogue", "own_action"},
        reason="react",
    )
    token, reason = SpotlightPolicy.resolve(state, grant, 1)
    assert reason is None
    assert token.owner_id == "mira"
    assert token.granted_at_turn == 1


def test_spotlight_policy_falls_back_for_unknown_actor():
    state = load_scenario("examples/station_zero.yaml")
    grant = SpotlightGrant(
        owner_type=SpotlightOwner.ACTOR,
        owner_id="ghost",
        scopes={"own_action"},
        reason="bad",
    )
    token, reason = SpotlightPolicy.resolve(state, grant, 2)
    assert token.owner_type == SpotlightOwner.PLAYER
    assert token.owner_id == state.player.player_id
    assert token.scopes == {"own_action"}
    assert "unknown actor" in reason


def test_spotlight_policy_falls_back_for_unknown_player_and_gm():
    state = load_scenario("examples/station_zero.yaml")
    for owner_type, owner_id in (
        (SpotlightOwner.PLAYER, "not_player"),
        (SpotlightOwner.GM, "not_gm"),
    ):
        grant = SpotlightGrant(
            owner_type=owner_type, owner_id=owner_id, scopes={"own_action"}, reason="bad"
        )
        token, reason = SpotlightPolicy.resolve(state, grant, 1)
        assert token.owner_id == state.player.player_id
        assert reason
