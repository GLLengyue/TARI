from __future__ import annotations

import time
from abc import ABC, abstractmethod

from .config import RuntimeConfig
from .domain import (
    ActorTurn,
    ActorView,
    AuditResult,
    AuditViolation,
    GMDecision,
    GMView,
    Outcome,
    RollResult,
    SpotlightGrant,
    SpotlightOwner,
    StatePatch,
)


class AgentSuite(ABC):
    @abstractmethod
    async def gm_plan(self, view: GMView, player_input: str) -> GMDecision: ...

    @abstractmethod
    async def gm_resolve(self, view: GMView, player_input: str, roll: RollResult | None) -> GMDecision: ...

    @abstractmethod
    async def actor_turn(self, view: ActorView) -> ActorTurn: ...

    @abstractmethod
    async def audit(self, view: ActorView, turn: ActorTurn) -> AuditResult: ...


class PydanticAISuite(AgentSuite):
    def __init__(self, config: RuntimeConfig):
        try:
            from pydantic_ai import Agent
            from pydantic_ai.settings import ModelSettings
        except ImportError as exc:
            raise RuntimeError("Install project dependencies to use cloud agents: pip install -e .") from exc

        def make(name: str, output_type, instructions: str):
            ac = config.agents[name]
            mc = config.models[ac.model]
            settings = ModelSettings(temperature=ac.temperature, max_tokens=ac.max_output_tokens)
            return Agent(mc.model_id, output_type=output_type, instructions=instructions, model_settings=settings)

        self.gm = make("gm", GMDecision, """You are a fair TRPG game master. Return typed decisions only. Never roll dice. Never decide unspoken player thoughts or actions. Checks use 2d6 with no modifiers or difficulty. Before a roll, define stakes. Patches may be proposed only for scene, actors, or status paths. Keep secrets out of public narration.""")
        self.actor = make("actor", ActorTurn, """You are the spotlighted NPC actor. Use only the supplied ActorView. You may speak and state your own intended action. Never announce unadjudicated success, control the player, assign spotlight, create dice, or change world state.""")
        self.auditor = make("auditor", AuditResult, """Audit an NPC performance. Reject control of the player, claimed world outcomes, use of unavailable knowledge, or conflict with spotlight scope. Be concise.""")

    async def gm_plan(self, view, player_input):
        prompt = (
            "State and private GM view:\n"
            + view.model_dump_json()
            + "\nPlayer action:\n"
            + player_input
            + "\nDecide whether a check is needed. If no check is needed, resolve directly."
        )
        result = await self.gm.run(prompt)
        return result.output

    async def gm_resolve(self, view, player_input, roll):
        roll_text = roll.model_dump_json() if roll else "No check was required."
        prompt = (
            "State and private GM view:\n"
            + view.model_dump_json()
            + "\nPlayer action:\n"
            + player_input
            + "\nAuthoritative roll:\n"
            + roll_text
            + "\nResolve without altering the roll. If an actor should react, grant actor "
            "spotlight to an existing actor ID; otherwise return player spotlight."
        )
        result = await self.gm.run(prompt)
        return result.output

    async def actor_turn(self, view):
        result = await self.actor.run(view.model_dump_json())
        return result.output

    async def audit(self, view, turn):
        prompt = "Actor view:\n" + view.model_dump_json() + "\nProposed turn:\n" + turn.model_dump_json()
        result = await self.auditor.run(prompt)
        return result.output


class FakeAgentSuite(AgentSuite):
    async def gm_plan(self, view: GMView, player_input: str) -> GMDecision:
        actor_id = next(iter(view.state.actors))
        lowered = player_input.lower()
        needs_check = any(k in lowered for k in ["check", "inspect", "listen", "open", "search", "检查", "倾听", "打开", "搜索"])
        check = None
        if needs_check:
            from .domain import CheckRequest
            check = CheckRequest(
                actor_id=view.state.player.player_id,
                move="act_under_uncertainty",
                reason="The action has uncertain consequences.",
                stakes_on_full_success="The player achieves the intent cleanly.",
                stakes_on_success_with_cost="The player succeeds but attracts danger or pays a cost.",
                stakes_on_failure="The attempt fails and the situation worsens.",
            )
        return GMDecision(
            reasoning_summary="Fake deterministic GM planning.",
            check_request=check,
            next_spotlight=SpotlightGrant(owner_type=SpotlightOwner.GM, owner_id="gm", scopes={"public_narration", "world_consequence"}, reason="resolve action"),
        )

    async def gm_resolve(self, view, player_input, roll):
        actor_id = next(iter(view.state.actors))
        if roll and roll.outcome == Outcome.FULL_SUCCESS:
            text = "You uncover the clue without giving away your interest."
        elif roll and roll.outcome == Outcome.SUCCESS_WITH_COST:
            text = "You uncover the clue, but the console emits a sharp warning tone."
        elif roll:
            text = "The attempt goes wrong; footsteps in the corridor abruptly stop."
        else:
            text = "The world shifts in response to your declared action."
        patches = [StatePatch(operation="add", path="scene.public_facts", new_value=text, reason="resolved player action", proposed_by="gm")]
        return GMDecision(
            reasoning_summary="Apply the authoritative PbtA result.",
            public_narration=text,
            proposed_state_patches=patches,
            actor_observations={actor_id: ["The player has focused on the control console."]},
            next_spotlight=SpotlightGrant(owner_type=SpotlightOwner.ACTOR, owner_id=actor_id, scopes={"own_dialogue", "own_action", "private_thought"}, reason="NPC reacts"),
        )

    async def actor_turn(self, view):
        return ActorTurn(
            speech="Don't touch that page.",
            action=f"{view.actor.name} steps between the player and the console.",
            private_thought="The player noticed too much.",
            intent="Delay the investigation without openly confessing knowledge.",
        )

    async def audit(self, view, turn):
        combined = f"{turn.speech or ''} {turn.action or ''}".lower()
        bad = any(x in combined for x in ["the player decides", "the player feels", "successfully traps"])
        return AuditResult(
            accepted=not bad,
            violations=[] if not bad else [AuditViolation(code="narrative_overreach", message="Actor controlled player or established outcome")],
            retry_instruction=None if not bad else "Describe only the actor's own attempted action.",
        )
