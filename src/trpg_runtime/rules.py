from __future__ import annotations

import copy
import random
from typing import Any

from .domain import (
    CampaignState,
    Outcome,
    RollResult,
    SpotlightGrant,
    SpotlightOwner,
    SpotlightToken,
    StatePatch,
)


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


class SpotlightPolicy:
    """GM proposes the next spotlight; the runtime validates and falls back.

    An invalid or missing proposal (unknown owner) never fails the turn:
    the spotlight returns to the player with ``own_action``, and the reason is
    recorded for auditing.
    """

    @staticmethod
    def resolve(
        state: CampaignState, grant: SpotlightGrant, turn: int
    ) -> tuple[SpotlightToken, str | None]:
        reason: str | None = None
        if grant.owner_type == SpotlightOwner.ACTOR and grant.owner_id not in state.actors:
            reason = f"unknown actor: {grant.owner_id}"
        elif grant.owner_type == SpotlightOwner.PLAYER and grant.owner_id != state.player.player_id:
            reason = f"unknown player: {grant.owner_id}"
        elif grant.owner_type == SpotlightOwner.GM and grant.owner_id != "gm":
            reason = f"unknown gm: {grant.owner_id}"
        if reason is not None:
            return (
                SpotlightToken(
                    owner_type=SpotlightOwner.PLAYER,
                    owner_id=state.player.player_id,
                    scopes={"own_action"},
                    granted_at_turn=turn,
                    reason="policy fallback",
                ),
                reason,
            )
        return SpotlightManager.grant(grant, turn), None


_ALLOWED_GM_PREFIXES = ("scene.", "actors.", "status")


def _normalize_path(path: str) -> str:
    """Normalize model-written patch paths to dot notation.

    Accepts '/actors/mira/...', '"scene.x"', or 'scene.x' forms.
    """
    return path.strip().strip('"').strip("'").replace("/", ".").strip(".")


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
        path = _normalize_path(patch.path)
        if not path.startswith(_ALLOWED_GM_PREFIXES):
            raise RuleViolation(f"forbidden patch path: {path}")
        parent: Any
        key: Any
        parent, key = _resolve_parent(trial, path)
        existing: Any = parent.get(key) if isinstance(parent, dict) else parent[int(key)]
        if patch.old_value is not None and existing != patch.old_value:
            raise RuleViolation(f"old value mismatch at {path}")
        if patch.operation == "set":
            parent[key] = patch.new_value
        elif patch.operation == "add":
            if not isinstance(existing, list):
                raise RuleViolation(f"add target is not a list at {path}")
            existing.append(patch.new_value)
        elif patch.operation == "remove":
            if not isinstance(existing, list):
                raise RuleViolation(f"remove target is not a list at {path}")
            existing.remove(patch.new_value)
        elif patch.operation == "increment":
            if not isinstance(existing, (int, float)) or not isinstance(
                patch.new_value, (int, float)
            ):
                raise RuleViolation(f"increment target is not numeric at {path}")
            parent[key] = existing + patch.new_value
    trial["version"] = state.version + 1
    return CampaignState.model_validate(trial)
