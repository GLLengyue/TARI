from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .agents import AgentSuite
from .domain import (
    CampaignState,
    GMDecision,
    GMView,
    RollResult,
    SpotlightGrant,
    SpotlightOwner,
    TurnResult,
)
from .projections import build_actor_view, build_gm_view
from .rules import (
    DiceEngine,
    RuleViolation,
    SpotlightManager,
    SpotlightPolicy,
    apply_patches,
)
from .storage import EventStore, TurnTransaction


class TurnOrchestrator:
    def __init__(
        self,
        store: EventStore,
        agents: AgentSuite,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.store = store
        self.agents = agents
        self.on_progress = on_progress

    def _progress(self, stage: str, **payload: Any) -> None:
        if self.on_progress is not None:
            self.on_progress(stage, payload)

    async def process_turn(
        self,
        state: CampaignState,
        player_input: str,
        request_id: str | None = None,
    ) -> tuple[CampaignState, TurnResult]:
        """Run one turn atomically.

        All events and the snapshot commit in a single transaction; on any
        failure only a ``turn_aborted`` audit event is persisted. When
        ``request_id`` is provided, replaying it returns the cached result
        instead of re-running the turn.
        """
        if request_id is not None:
            cached = self.store.load_turn_result(request_id)
            if cached is not None:
                return self.store.load_snapshot(state.campaign_id), cached

        SpotlightManager.require(state, state.player.player_id, "own_action")
        turn = state.turn_number + 1
        tx = self.store.begin_turn(state.campaign_id, turn)
        try:
            new_state, result = await self._run_turn(tx, state, turn, player_input)
        except Exception as exc:
            tx.abort(str(exc))
            raise
        tx.commit(new_state, request_id=request_id, result=result)
        self._progress("completed", turn=turn)
        return new_state, result

    async def _run_turn(
        self,
        tx: TurnTransaction,
        state: CampaignState,
        turn: int,
        player_input: str,
    ) -> tuple[CampaignState, TurnResult]:
        recent = [
            e["payload"].get("text", "")
            for e in self.store.events(state.campaign_id)
            if e["type"] == "public_narrative_emitted"
        ]
        self._progress("player_action", text=player_input)
        tx.append("player_action_received", {"text": player_input})

        gm_view = build_gm_view(state, recent)
        plan = await self._gm_plan(tx, gm_view, player_input)

        roll = await self._maybe_roll(tx, state, plan)

        resolution = await self._gm_resolve(tx, gm_view, player_input, roll)
        new_state, gm_text, debug = self._commit_resolution(tx, state, turn, resolution, plan)

        actor_speech, actor_action = await self._actor_stage(
            tx, new_state, resolution, gm_text, recent, debug
        )

        new_state.spotlight = SpotlightManager.grant(
            SpotlightGrant(
                owner_type=SpotlightOwner.PLAYER,
                owner_id=new_state.player.player_id,
                scopes={"own_action"},
                reason="turn complete",
            ),
            turn,
        )
        tx.append("turn_completed", {"version": new_state.version})
        result = TurnResult(
            campaign_id=state.campaign_id,
            turn_number=turn,
            player_input=player_input,
            roll=roll,
            gm_narration=gm_text,
            actor_speech=actor_speech,
            actor_action=actor_action,
            debug=debug,
        )
        return new_state, result

    async def _gm_plan(self, tx: TurnTransaction, gm_view: GMView, player_input: str) -> GMDecision:
        self._progress("gm_planning")
        plan = await self.agents.gm_plan(gm_view, player_input)
        tx.append("gm_decision_proposed", plan.model_dump(mode="json"))
        for call in self.agents.drain_tool_calls():
            tx.append("tool_called", call)
        return plan

    async def _maybe_roll(
        self, tx: TurnTransaction, state: CampaignState, plan: GMDecision
    ) -> RollResult | None:
        if plan.check_request is None:
            return None
        tx.append("check_requested", plan.check_request.model_dump(mode="json"))
        dice = DiceEngine(state.seed)
        # Advance deterministic stream by replaying prior committed rolls.
        prior = sum(1 for e in self.store.events(state.campaign_id) if e["type"] == "dice_rolled")
        for _ in range(prior):
            dice.roll_pbta(plan.check_request.check_id)
        self._progress("rolling")
        roll = dice.roll_pbta(plan.check_request.check_id)
        tx.append("dice_rolled", roll.model_dump(mode="json"))
        self._progress(
            "dice",
            rolls=roll.rolls,
            total=roll.total,
            outcome=roll.outcome.value,
        )
        return roll

    async def _gm_resolve(
        self,
        tx: TurnTransaction,
        gm_view: GMView,
        player_input: str,
        roll: RollResult | None,
    ) -> GMDecision:
        self._progress("gm_resolving")
        resolution = await self.agents.gm_resolve(gm_view, player_input, roll)
        for call in self.agents.drain_tool_calls():
            tx.append("tool_called", call)
        return resolution

    def _commit_resolution(
        self,
        tx: TurnTransaction,
        state: CampaignState,
        turn: int,
        resolution: GMDecision,
        plan: GMDecision,
    ) -> tuple[CampaignState, str | None, dict[str, Any]]:
        new_state = apply_patches(state, resolution.proposed_state_patches)
        new_state.turn_number = turn
        self._progress("committing")
        tx.append(
            "state_patch_committed",
            {
                "patches": [p.model_dump(mode="json") for p in resolution.proposed_state_patches],
                "version": new_state.version,
            },
        )
        gm_text = resolution.public_narration
        if gm_text:
            tx.append(
                "public_narrative_emitted",
                {"speaker": "gm", "text": gm_text},
            )
        debug = {
            "plan": plan.model_dump(mode="json"),
            "resolution": resolution.model_dump(mode="json"),
        }
        return new_state, gm_text, debug

    async def _actor_stage(
        self,
        tx: TurnTransaction,
        new_state: CampaignState,
        resolution: GMDecision,
        gm_text: str | None,
        recent: list[str],
        debug: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        grant = resolution.next_spotlight
        token, fallback_reason = SpotlightPolicy.resolve(new_state, grant, new_state.turn_number)
        if fallback_reason:
            tx.append(
                "spotlight_policy_fallback",
                {"proposal": grant.model_dump(mode="json"), "reason": fallback_reason},
            )
        new_state.spotlight = token
        tx.append("spotlight_granted", grant.model_dump(mode="json"))

        if token.owner_type != SpotlightOwner.ACTOR:
            return None, None

        actor_id = token.owner_id
        view = build_actor_view(
            new_state,
            actor_id,
            resolution.actor_observations.get(actor_id, []),
            recent + ([gm_text] if gm_text else []),
        )
        self._progress("actor_turn", actor_id=actor_id)
        performance = await self.agents.actor_turn(view)
        self._progress("auditing")
        audit = await self.agents.audit(view, performance)
        tx.append("actor_turn_proposed", performance.model_dump(mode="json"))
        tx.append("audit_completed", audit.model_dump(mode="json"))
        if not audit.accepted:
            raise RuleViolation(f"actor audit rejected: {audit.violations}")
        actor_speech, actor_action = performance.speech, performance.action
        public = " ".join(
            x for x in [actor_action, f"“{actor_speech}”" if actor_speech else None] if x
        )
        tx.append(
            "public_narrative_emitted",
            {"speaker": actor_id, "text": public},
        )
        debug["actor_view"] = view.model_dump(mode="json")
        debug["actor_turn"] = performance.model_dump(mode="json")
        debug["audit"] = audit.model_dump(mode="json")
        return actor_speech, actor_action
