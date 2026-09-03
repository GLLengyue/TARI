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
from .projections import compact_gm_view

GM_REQUEST_LIMIT = 20
ACTOR_REQUEST_LIMIT = 20
WRAP_REQUEST_LIMIT = 8


def prefix_delta(previous: str, current: str) -> tuple[str, str]:
    """Return ``(delta, new_previous)`` for streaming text fields.

    When ``current`` extends ``previous``, only the appended suffix is
    returned.  If the stream ever resets or rewrites the value, the whole
    ``current`` value is returned so the frontend can replace its buffer.
    """
    previous = previous or ""
    current = current or ""
    if current.startswith(previous):
        return current[len(previous) :], current
    return current, current


class TokenEmitter:
    """Tracks per-channel text so partial outputs translate to deltas."""

    def __init__(self, on_token) -> None:
        self.on_token = on_token
        self._last: dict[str, str] = {}

    def emit(self, channel: str, value: str) -> None:
        delta, self._last[channel] = prefix_delta(self._last.get(channel, ""), value)
        if delta:
            self.on_token(channel, delta)


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

    async def gm_plan_stream(self, view: GMView, player_input: str, emit) -> GMDecision:
        """Stream the GM plan's reasoning, then return the authoritative decision."""
        decision = await self.gm_plan(view, player_input)
        emit("gm_reasoning", decision.reasoning_summary or "")
        return decision

    async def gm_resolve_stream(self, view: GMView, player_input: str, roll, emit) -> GMDecision:
        """Stream the GM resolution's reasoning and narration."""
        decision = await self.gm_resolve(view, player_input, roll)
        emit("gm_reasoning", decision.reasoning_summary or "")
        if decision.public_narration:
            emit("gm_narration", decision.public_narration)
        return decision

    async def actor_turn_stream(self, view: ActorView, emit) -> ActorTurn:
        """Stream the actor's speech and intended action."""
        performance = await self.actor_turn(view)
        if performance.speech:
            emit("actor_speech", performance.speech)
        if performance.action:
            emit("actor_action", performance.action)
        return performance

    async def gm_wrap(
        self,
        view: GMView,
        player_input: str,
        roll,
        resolution: GMDecision,
        performance: ActorTurn,
    ) -> str | None:
        """Compose the short scene continuation after the actor performed.

        The default implementation adds nothing; cloud suites override this to
        weave world/secondary-NPC reactions around the actor's own output.
        """
        return None


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
            f"notation and one of the operations set/add/remove/increment (no slashes, no "
            f"quotes). scene.public_facts and scene.hidden_facts are lists: append a fact by "
            f"using operation add with path scene.public_facts and the fact text as new_value "
            f"- never put fact text or an index in the path itself. Dict paths such as "
            f"actors.<id>.attributes.<name> or status.<key> use set/increment.\n"
            f"- Keep secrets out of public narration.\n"
            f"- public_narration is third-person scene/world narration only: environment, "
            f"weather, sounds, and secondary NPCs (e.g. passers-by). Never write the main "
            f"actor's dialogue or actions - the actor agent performs those.\n"
            f"- Grant actor spotlight when the player's action targets the main actor or "
            f"the main actor would plausibly react; skip it when the action concerns only "
            f"the environment or secondary NPCs.\n"
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
        self.wrap = make(
            "gm",
            str,
            f"You are a fair TRPG game master writing a short scene continuation. "
            f"Compose third-person narration about how the world, environment, or secondary "
            f"NPCs react around the main actor's performance. Never rewrite the main actor's "
            f"speech or actions. Return only the narration text; return an empty string if "
            f"nothing is needed. {lang}",
        )

    def _run_gm(self, agent, prompt: str, view: GMView):
        deps = GMDocDeps(registry=build_registry(view.state), state=view.state)
        from pydantic_ai.usage import UsageLimits

        return (
            agent.run(
                prompt,
                deps=deps,
                usage_limits=UsageLimits(request_limit=GM_REQUEST_LIMIT),
            ),
            deps,
        )

    async def _run_gm_stream(
        self,
        agent,
        prompt: str,
        view: GMView,
        emit,
        fields: tuple[tuple[str, str], ...],
    ):
        """Run an agent with streaming partial outputs and return the typed result."""
        deps = GMDocDeps(registry=build_registry(view.state), state=view.state)
        from pydantic_ai.usage import UsageLimits

        emitter = TokenEmitter(emit)
        async with agent.run_stream(
            prompt,
            deps=deps,
            usage_limits=UsageLimits(request_limit=GM_REQUEST_LIMIT),
        ) as stream:
            async for partial in stream.stream_output(debounce_by=0.0):
                for attr, channel in fields:
                    value = getattr(partial, attr, None)
                    if isinstance(value, str):
                        emitter.emit(channel, value)
            output = await stream.get_output()
        return output, deps

    def drain_tool_calls(self) -> list[dict]:
        calls, self._pending_tool_calls = self._pending_tool_calls, []
        return calls

    async def gm_plan(self, view, player_input):
        prompt = (
            "State and private GM view:\n"
            + compact_gm_view(view)
            + "\nPlayer action:\n"
            + player_input
            + "\nDecide whether a check is needed. If no check is needed, resolve directly."
        )
        coro, deps = self._run_gm(self.gm, prompt, view)
        result = await coro
        self._pending_tool_calls.extend(deps.calls)
        return result.output

    async def gm_plan_stream(self, view, player_input, emit):
        prompt = (
            "State and private GM view:\n"
            + compact_gm_view(view)
            + "\nPlayer action:\n"
            + player_input
            + "\nDecide whether a check is needed. If no check is needed, resolve directly."
        )
        output, deps = await self._run_gm_stream(
            self.gm,
            prompt,
            view,
            emit,
            (("reasoning_summary", "gm_reasoning"),),
        )
        self._pending_tool_calls.extend(deps.calls)
        return output

    async def gm_resolve(self, view, player_input, roll):
        roll_text = roll.model_dump_json() if roll else "No check was required."
        prompt = (
            "State and private GM view:\n"
            + compact_gm_view(view)
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

    async def gm_resolve_stream(self, view, player_input, roll, emit):
        roll_text = roll.model_dump_json() if roll else "No check was required."
        prompt = (
            "State and private GM view:\n"
            + compact_gm_view(view)
            + "\nPlayer action:\n"
            + player_input
            + "\nAuthoritative roll:\n"
            + roll_text
            + "\nResolve without altering the roll. If an actor should react, grant actor "
            "spotlight to an existing actor ID; otherwise return player spotlight."
        )
        output, deps = await self._run_gm_stream(
            self.gm,
            prompt,
            view,
            emit,
            (("reasoning_summary", "gm_reasoning"), ("public_narration", "gm_narration")),
        )
        self._pending_tool_calls.extend(deps.calls)
        return output

    async def actor_turn(self, view):
        result = await self.actor.run(view.model_dump_json())
        return result.output

    async def actor_turn_stream(self, view, emit):
        emitter = TokenEmitter(emit)
        from pydantic_ai.usage import UsageLimits

        async with self.actor.run_stream(
            view.model_dump_json(),
            usage_limits=UsageLimits(request_limit=ACTOR_REQUEST_LIMIT),
        ) as stream:
            async for partial in stream.stream_output(debounce_by=0.0):
                for attr, channel in (("speech", "actor_speech"), ("action", "actor_action")):
                    value = getattr(partial, attr, None)
                    if isinstance(value, str):
                        emitter.emit(channel, value)
            output = await stream.get_output()
        return output

    async def gm_wrap(self, view, player_input, roll, resolution, performance):
        roll_text = roll.model_dump_json() if roll else "No check was required."
        prompt = (
            "State and private GM view:\n"
            + compact_gm_view(view)
            + "\nPlayer action:\n"
            + player_input
            + "\nAuthoritative roll:\n"
            + roll_text
            + "\nGM scene resolution so far:\n"
            + (resolution.public_narration or "(none)")
            + "\nMain actor performance (produced by the actor agent):\n"
            + performance.model_dump_json()
            + "\nWrite ONLY a short third-person scene continuation: how the world, "
            "environment, or secondary NPCs react around the actor's performance. Do NOT "
            "rewrite the main actor's speech or actions. Return plain narration text; if "
            "nothing is needed, return an empty string."
        )
        from pydantic_ai.usage import UsageLimits

        result = await self.wrap.run(
            prompt, usage_limits=UsageLimits(request_limit=WRAP_REQUEST_LIMIT)
        )
        text = (result.output or "").strip()
        return text or None

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
