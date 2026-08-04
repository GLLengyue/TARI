from __future__ import annotations

import copy
import random
from typing import Any

from .domain import CampaignState, Outcome, RollResult, SpotlightGrant, SpotlightToken, StatePatch


class RuleViolation(ValueError):
    pass


class DiceEngine:
    def __init__(self, seed: int):
        self._rng = random.Random(seed)

    def roll_pbta(self, check_id):
        rolls = (self._rng.randint(1, 6), self._rng.randint(1, 6))
        total = sum(rolls)
        if total >= 10:
            outcome = Outcome.FULL_SUCCESS
        elif total >= 7:
            outcome = Outcome.SUCCESS_WITH_COST
        else:
            outcome = Outcome.FAILURE
        return RollResult(check_id=check_id, rolls=rolls, total=total, outcome=outcome)


class SpotlightManager:
    @staticmethod
    def grant(grant: SpotlightGrant, turn: int) -> SpotlightToken:
        return SpotlightToken(
            owner_type=grant.owner_type,
            owner_id=grant.owner_id,
            scopes=grant.scopes,
            granted_at_turn=turn,
            reason=grant.reason,
        )

    @staticmethod
    def require(state: CampaignState, owner_id: str, required_scope: str) -> None:
        if state.spotlight.owner_id != owner_id:
            raise RuleViolation(f"{owner_id} does not own spotlight")
        if required_scope not in state.spotlight.scopes:
            raise RuleViolation(f"spotlight lacks scope {required_scope}")


_ALLOWED_GM_PREFIXES = ("scene.", "actors.", "status")


def _resolve_parent(document: Any, path: str):
    parts = path.split(".")
    current = document
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current, parts[-1]


def apply_patches(state: CampaignState, patches: list[StatePatch]) -> CampaignState:
    raw = state.model_dump(mode="python")
    trial = copy.deepcopy(raw)
    for patch in patches:
        if patch.proposed_by != "gm":
            raise RuleViolation("only GM patches are accepted in the MVP")
        if not patch.path.startswith(_ALLOWED_GM_PREFIXES):
            raise RuleViolation(f"forbidden patch path: {patch.path}")
        parent, key = _resolve_parent(trial, patch.path)
        existing = parent.get(key) if isinstance(parent, dict) else parent[int(key)]
        if patch.old_value is not None and existing != patch.old_value:
            raise RuleViolation(f"old value mismatch at {patch.path}")
        if patch.operation == "set":
            parent[key] = patch.new_value
        elif patch.operation == "add":
            existing.append(patch.new_value)
        elif patch.operation == "remove":
            existing.remove(patch.new_value)
        elif patch.operation == "increment":
            parent[key] = existing + patch.new_value
    trial["version"] = state.version + 1
    return CampaignState.model_validate(trial)
