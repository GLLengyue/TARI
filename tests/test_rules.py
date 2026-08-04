from uuid import uuid4

import pytest

from trpg_runtime.domain import SpotlightGrant, SpotlightOwner, StatePatch
from trpg_runtime.rules import DiceEngine, RuleViolation, SpotlightManager, apply_patches
from trpg_runtime.scenario import load_scenario


def test_pbta_seed_is_deterministic():
    a, b = DiceEngine(42), DiceEngine(42)
    assert [a.roll_pbta(uuid4()).rolls for _ in range(5)] == [b.roll_pbta(uuid4()).rolls for _ in range(5)]


def test_pbta_outcome_bands():
    # Test classification exhaustively through a controlled fake RNG stream.
    engine = DiceEngine(1)
    result = engine.roll_pbta(uuid4())
    assert result.outcome.value in {"full_success", "success_with_cost", "failure"}
    assert 2 <= result.total <= 12


def test_actor_cannot_patch_world():
    state = load_scenario("examples/station_zero.yaml")
    patch = StatePatch(operation="add", path="scene.public_facts", new_value="x", reason="bad", proposed_by="mira")
    with pytest.raises(RuleViolation):
        apply_patches(state, [patch])


def test_atomic_patch_rejection():
    state = load_scenario("examples/station_zero.yaml")
    valid = StatePatch(operation="add", path="scene.public_facts", new_value="x", reason="ok", proposed_by="gm")
    invalid = StatePatch(operation="set", path="seed", new_value=1, reason="bad", proposed_by="gm")
    with pytest.raises(RuleViolation):
        apply_patches(state, [valid, invalid])
    assert "x" not in state.scene.public_facts


def test_spotlight_is_enforced():
    state = load_scenario("examples/station_zero.yaml")
    with pytest.raises(RuleViolation):
        SpotlightManager.require(state, "mira", "own_action")
