from __future__ import annotations

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
from .gm_docs import (
    GMDocDeps,
    build_registry,
    gm_get_character_card,
    gm_get_scenario_outline,
    gm_search_rules,
    gm_search_world,
)
from .i18n import DEFAULT_LOCALE, language_instruction


class AgentSuite(ABC):
    @abstractmethod
    async def gm_plan(self, view: GMView, player_input: str) -> GMDecision: ...

    @abstractmethod
    async def gm_resolve(
        self, view: GMView, player_input: str, roll: RollResult | None
    ) -> GMDecision: ...

    @abstractmethod
    async def actor_turn(self, view: ActorView) -> ActorTurn: ...

    @abstractmethod
    async def audit(self, view: ActorView, turn: ActorTurn) -> AuditResult: ...

    def drain_tool_calls(self) -> list[dict]:
        """Return and clear tool calls recorded by the last agent run(s)."""
        return []


class PydanticAISuite(AgentSuite):
    def __init__(self, config: RuntimeConfig, locale: str = DEFAULT_LOCALE):
        self._pending_tool_calls: list[dict] = []
        try:
            from pydantic_ai import Agent
            from pydantic_ai.settings import ModelSettings
        except ImportError as exc:
            raise RuntimeError(
                "Install project dependencies to use cloud agents: pip install -e ."
            ) from exc

        def make(name: str, output_type, instructions: str, *, tools=(), deps_type=None):
            ac = config.agents[name]
            mc = config.models[ac.model]
            settings = ModelSettings(temperature=ac.temperature, max_tokens=ac.max_output_tokens)
            return Agent(
                mc.model_id,
                output_type=output_type,
                instructions=instructions,
                model_settings=settings,
                tools=list(tools),
                deps_type=deps_type,
            )

        lang = language_instruction(locale)
        self.gm = make(
            "gm",
            GMDecision,
            f"You are a fair TRPG game master. Return typed decisions only. Never roll dice. "
            f"Never decide unspoken player thoughts or actions.\n"
            f"Rules:\n"
            f"- Checks use 2d6 with no modifiers or difficulty. Before a roll, define stakes.\n"
            f"- Outcome bands: 10+ full success; 7-9 success with a cost; 6 or lower failure.\n"
            f"- Call a check only when consequences are genuinely uncertain; otherwise resolve "
            f"directly.\n"
            f"- Patches may be proposed only for scene, actors, or status paths, using dot "
            f"notation, e.g. scene.public_facts or actors.mira.attributes.<name> "
            f"(no slashes, no quotes).\n"
            f"- Keep secrets out of public narration.\n"
            f"- The provided view already contains the full state, world facts, character cards, "
            f"and story framework.\n"
            f"Tools: use at most one tool call only when you need a detail not already in the view;"
            f" then produce your final typed decision. Do not repeat tool calls. {lang}",
            tools=(
                gm_search_rules,
                gm_search_world,
                gm_get_character_card,
                gm_get_scenario_outline,
            ),
            deps_type=GMDocDeps,
        )
        self.actor = make(
            "actor",
            ActorTurn,
            f"You are the spotlighted NPC actor. Use only the supplied ActorView. You may speak "
            f"and state your own intended action. Never announce unadjudicated success, control "
            f"the player, assign spotlight, create dice, or change world state. {lang}",
        )
        self.auditor = make(
            "auditor",
            AuditResult,
            f"Audit an NPC performance. Reject control of the player, claimed world outcomes, "
            f"use of unavailable knowledge, or conflict with spotlight scope. Be concise. {lang}",
        )

    def _run_gm(self, agent, prompt: str, view: GMView):
        deps = GMDocDeps(registry=build_registry(view.state), state=view.state)
        from pydantic_ai.usage import UsageLimits

        return (
            agent.run(
                prompt,
                deps=deps,
                usage_limits=UsageLimits(request_limit=10),
            ),
            deps,
        )

    def drain_tool_calls(self) -> list[dict]:
        calls, self._pending_tool_calls = self._pending_tool_calls, []
        return calls

    async def gm_plan(self, view, player_input):
        prompt = (
            "State and private GM view:\n"
            + view.model_dump_json()
            + "\nPlayer action:\n"
            + player_input
            + "\nDecide whether a check is needed. If no check is needed, resolve directly."
        )
        coro, deps = self._run_gm(self.gm, prompt, view)
        result = await coro
        self._pending_tool_calls.extend(deps.calls)
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
        coro, deps = self._run_gm(self.gm, prompt, view)
        result = await coro
        self._pending_tool_calls.extend(deps.calls)
        return result.output

    async def actor_turn(self, view):
        result = await self.actor.run(view.model_dump_json())
        return result.output

    async def audit(self, view, turn):
        prompt = (
            "Actor view:\n" + view.model_dump_json() + "\nProposed turn:\n" + turn.model_dump_json()
        )
        result = await self.auditor.run(prompt)
        return result.output


_FAKE_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "reasoning": "Fake deterministic GM planning.",
        "check_reason": "The action has uncertain consequences.",
        "stakes_full": "The player achieves the intent cleanly.",
        "stakes_cost": "The player succeeds but attracts danger or pays a cost.",
        "stakes_failure": "The attempt fails and the situation worsens.",
        "resolve_full": "You uncover the clue without giving away your interest.",
        "resolve_cost": "You uncover the clue, but the console emits a sharp warning tone.",
        "resolve_failure": "The attempt goes wrong; footsteps in the corridor abruptly stop.",
        "resolve_other": "The world shifts in response to your declared action.",
        "observation": "The player has focused on the control console.",
        "speech": "Don't touch that page.",
        "action": "{name} steps between the player and the console.",
        "thought": "The player noticed too much.",
        "intent": "Delay the investigation without openly confessing knowledge.",
    },
    "zh": {
        "reasoning": "确定性假 GM 规划。",
        "check_reason": "这个行动的结果具有不确定性。",
        "stakes_full": "玩家干净利落地达成了意图。",
        "stakes_cost": "玩家成功，但引来危险或付出代价。",
        "stakes_failure": "尝试失败，局势恶化。",
        "resolve_full": "你发现了线索，而没有暴露自己的兴趣。",
        "resolve_cost": "你发现了线索，但控制台发出一声刺耳的警报。",
        "resolve_failure": "行动出了差错，走廊里的脚步声戛然而止。",
        "resolve_other": "世界对你的行动作出了回应。",
        "observation": "玩家一直在关注控制台。",
        "speech": "别碰那一页。",
        "action": "{name} 挡在了玩家与控制台之间。",
        "thought": "玩家注意到了太多东西。",
        "intent": "拖延调查，但不要公开承认自己知道什么。",
    },
}


class FakeAgentSuite(AgentSuite):
    def __init__(self, locale: str = DEFAULT_LOCALE):
        self.texts = _FAKE_TEXTS.get(locale, _FAKE_TEXTS[DEFAULT_LOCALE])

    async def gm_plan(self, view: GMView, player_input: str) -> GMDecision:
        lowered = player_input.lower()
        needs_check = any(
            k in lowered
            for k in [
                "check",
                "inspect",
                "listen",
                "open",
                "search",
                "检查",
                "倾听",
                "打开",
                "搜索",
            ]
        )
        check = None
        if needs_check:
            from .domain import CheckRequest

            check = CheckRequest(
                actor_id=view.state.player.player_id,
                move="act_under_uncertainty",
                reason=self.texts["check_reason"],
                stakes_on_full_success=self.texts["stakes_full"],
                stakes_on_success_with_cost=self.texts["stakes_cost"],
                stakes_on_failure=self.texts["stakes_failure"],
            )
        return GMDecision(
            reasoning_summary=self.texts["reasoning"],
            check_request=check,
            next_spotlight=SpotlightGrant(
                owner_type=SpotlightOwner.GM,
                owner_id="gm",
                scopes={"public_narration", "world_consequence"},
                reason="resolve action",
            ),
        )

    async def gm_resolve(self, view, player_input, roll):
        actor_id = next(iter(view.state.actors))
        if roll and roll.outcome == Outcome.FULL_SUCCESS:
            text = self.texts["resolve_full"]
        elif roll and roll.outcome == Outcome.SUCCESS_WITH_COST:
            text = self.texts["resolve_cost"]
        elif roll:
            text = self.texts["resolve_failure"]
        else:
            text = self.texts["resolve_other"]
        patches = [
            StatePatch(
                operation="add",
                path="scene.public_facts",
                new_value=text,
                reason="resolved player action",
                proposed_by="gm",
            )
        ]
        return GMDecision(
            reasoning_summary=self.texts["reasoning"],
            public_narration=text,
            proposed_state_patches=patches,
            actor_observations={actor_id: [self.texts["observation"]]},
            next_spotlight=SpotlightGrant(
                owner_type=SpotlightOwner.ACTOR,
                owner_id=actor_id,
                scopes={"own_dialogue", "own_action", "private_thought"},
                reason="NPC reacts",
            ),
        )

    async def actor_turn(self, view):
        return ActorTurn(
            speech=self.texts["speech"],
            action=self.texts["action"].format(name=view.actor.name),
            private_thought=self.texts["thought"],
            intent=self.texts["intent"],
        )

    async def audit(self, view, turn):
        combined = f"{turn.speech or ''} {turn.action or ''}".lower()
        bad = any(
            x in combined for x in ["the player decides", "the player feels", "successfully traps"]
        )
        return AuditResult(
            accepted=not bad,
            violations=[]
            if not bad
            else [
                AuditViolation(
                    code="narrative_overreach",
                    message="Actor controlled player or established outcome",
                )
            ],
            retry_instruction=None
            if not bad
            else "Describe only the actor's own attempted action.",
        )
