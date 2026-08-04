from __future__ import annotations

from .agents import AgentSuite
from .domain import (
    CampaignState,
    SpotlightGrant,
    SpotlightOwner,
    TurnResult,
)
from .projections import build_actor_view, build_gm_view
from .rules import DiceEngine, RuleViolation, SpotlightManager, apply_patches
from .storage import EventStore


class TurnOrchestrator:
    def __init__(self, store: EventStore, agents: AgentSuite):
        self.store = store
        self.agents = agents

    async def process_turn(self, state: CampaignState, player_input: str) -> tuple[CampaignState, TurnResult]:
        SpotlightManager.require(state, state.player.player_id, "own_action")
        turn = state.turn_number + 1
        recent = [e["payload"].get("text", "") for e in self.store.events(state.campaign_id) if e["type"] == "public_narrative_emitted"]
        self.store.append(state.campaign_id, turn, "player_action_received", {"text": player_input})

        gm_view = build_gm_view(state, recent)
        plan = await self.agents.gm_plan(gm_view, player_input)
        self.store.append(state.campaign_id, turn, "gm_decision_proposed", plan.model_dump(mode="json"))

        roll = None
        if plan.check_request:
            self.store.append(state.campaign_id, turn, "check_requested", plan.check_request.model_dump(mode="json"))
            dice = DiceEngine(state.seed)
            # Advance deterministic stream by replaying prior recorded rolls.
            prior = sum(1 for e in self.store.events(state.campaign_id) if e["type"] == "dice_rolled")
            for _ in range(prior):
                dice.roll_pbta(plan.check_request.check_id)
            roll = dice.roll_pbta(plan.check_request.check_id)
            self.store.append(state.campaign_id, turn, "dice_rolled", roll.model_dump(mode="json"))

        resolution = await self.agents.gm_resolve(gm_view, player_input, roll)
        new_state = apply_patches(state, resolution.proposed_state_patches)
        new_state.turn_number = turn
        self.store.append(state.campaign_id, turn, "state_patch_committed", {"patches": [p.model_dump(mode="json") for p in resolution.proposed_state_patches], "version": new_state.version})

        gm_text = resolution.public_narration
        if gm_text:
            self.store.append(state.campaign_id, turn, "public_narrative_emitted", {"speaker": "gm", "text": gm_text})

        actor_speech = actor_action = None
        debug = {"plan": plan.model_dump(mode="json"), "resolution": resolution.model_dump(mode="json")}
        grant = resolution.next_spotlight
        new_state.spotlight = SpotlightManager.grant(grant, turn)
        self.store.append(state.campaign_id, turn, "spotlight_granted", grant.model_dump(mode="json"))

        if grant.owner_type == SpotlightOwner.ACTOR:
            actor_id = grant.owner_id
            view = build_actor_view(new_state, actor_id, resolution.actor_observations.get(actor_id, []), recent + ([gm_text] if gm_text else []))
            performance = await self.agents.actor_turn(view)
            audit = await self.agents.audit(view, performance)
            self.store.append(state.campaign_id, turn, "actor_turn_proposed", performance.model_dump(mode="json"))
            self.store.append(state.campaign_id, turn, "audit_completed", audit.model_dump(mode="json"))
            if not audit.accepted:
                raise RuleViolation(f"actor audit rejected: {audit.violations}")
            actor_speech, actor_action = performance.speech, performance.action
            public = " ".join(x for x in [actor_action, f'“{actor_speech}”' if actor_speech else None] if x)
            self.store.append(state.campaign_id, turn, "public_narrative_emitted", {"speaker": actor_id, "text": public})
            debug["actor_view"] = view.model_dump(mode="json")
            debug["actor_turn"] = performance.model_dump(mode="json")
            debug["audit"] = audit.model_dump(mode="json")

        new_state.spotlight = SpotlightManager.grant(
            SpotlightGrant(owner_type=SpotlightOwner.PLAYER, owner_id=new_state.player.player_id, scopes={"own_action"}, reason="turn complete"), turn
        )
        self.store.append(state.campaign_id, turn, "turn_completed", {"version": new_state.version})
        self.store.save_snapshot(new_state)
        return new_state, TurnResult(
            campaign_id=state.campaign_id,
            turn_number=turn,
            player_input=player_input,
            roll=roll,
            gm_narration=gm_text,
            actor_speech=actor_speech,
            actor_action=actor_action,
            debug=debug,
        )
