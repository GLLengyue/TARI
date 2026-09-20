from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ..story.bundle import BeatChoice, StoryBeat, StoryBundle
from .domain import (
    ActionRuling,
    NarrativeAuthorProposal,
    NarrativeDraft,
    NarrativeStatePatch,
    PlayerIdentity,
    StorySessionState,
)
from .transitions import resolve_transition


class NarrativeAuthor(ABC):
    """Author contract for Story Mode.

    New authors return NarrativeDraft (prose and diagnostics only). Legacy
    proposals are checked against the runtime-resolved transition.

    ``rule_action`` and ``narrate_action`` carry freeform segments. Authors that
    only support beat transitions may leave them unimplemented; the runtime
    reports a clear violation when a segment needs them.
    """

    @abstractmethod
    async def generate(
        self,
        bundle: StoryBundle,
        state: StorySessionState,
        current_beat: StoryBeat,
        player_input: str,
        selected_choice: BeatChoice | None,
        recent_events: Sequence[dict[str, Any]],
    ) -> NarrativeDraft:
        raise NotImplementedError

    async def rule_action(
        self,
        messages: Sequence[dict[str, str]],
        action: str,
    ) -> tuple[ActionRuling, dict[str, Any]]:
        """Decide whether a freeform action holds, and what it causes."""
        raise NotImplementedError(f"{type(self).__name__} does not support freeform ruling")

    async def narrate_action(
        self,
        messages: Sequence[dict[str, str]],
        action: str,
        ruling: ActionRuling,
    ) -> str:
        """Write the scene for an already-ruled action."""
        raise NotImplementedError(f"{type(self).__name__} does not support freeform narration")

    async def aclose(self) -> None:
        """Release provider resources. Authors without a client may ignore this."""
        return None


class FakeNarrativeAuthor(NarrativeAuthor):
    """Offline author used by the vertical slice and regression tests."""

    #: Tokens the fake ruler treats as impossible in a pre-modern setting.
    impossible_tokens: tuple[str, ...] = ("手枪", "手机", "汽车", "gun", "pistol", "phone")

    def __init__(self, *, resolve_on: str | None = None):
        #: When the action text contains this substring, the fake ends the segment.
        self.resolve_on = resolve_on

    async def generate(
        self,
        bundle: StoryBundle,
        state: StorySessionState,
        current_beat: StoryBeat,
        player_input: str,
        selected_choice: BeatChoice | None,
        recent_events: Sequence[dict[str, Any]],
    ) -> NarrativeAuthorProposal:
        target = bundle.beat(selected_choice.next_beat_id) if selected_choice else current_beat
        proposal = resolve_transition(bundle, current_beat, player_input, selected_choice)
        narrative = target.narrative.strip()
        if selected_choice:
            narrative += "\n\n" + (
                selected_choice.narrative_hint
                or (f"Your decision echoes through the scene: {selected_choice.text}")
            )
        elif player_input.strip():
            narrative += "\n\nThe story receives your action: " + player_input.strip()
        proposal.narrative = narrative
        proposal.debug = {"author": "fake", "recent_event_count": len(recent_events)}
        return proposal

    async def rule_action(
        self,
        messages: Sequence[dict[str, str]],
        action: str,
    ) -> tuple[ActionRuling, dict[str, Any]]:
        blocked = next((token for token in self.impossible_tokens if token in action), None)
        resolved = self.resolve_on is not None and self.resolve_on in action
        if blocked is not None:
            ruling = ActionRuling(
                feasible=False,
                reason=f"fake ruler: {blocked} 不存在于这个世界",
                world_response="他的手指在腰间摸索了一圈，那里只有一柄小刀，别的什么都没有。",
                tension_resolved=False,
            )
        else:
            ruling = ActionRuling(
                feasible=True,
                reason="fake ruler: 行动成立",
                world_response=f"他照做了：{action}",
                consequences=[
                    NarrativeStatePatch(
                        operation="set",
                        path="variables.last_action",
                        new_value=action[:60],
                        reason="fake ruler: 记录玩家行动",
                    )
                ],
                tension_resolved=resolved,
                resolution=f"张力以「{self.resolve_on}」解决" if resolved else "",
            )
        debug = {"author": "fake-ruler", "blocked": blocked, "resolved": resolved}
        return ruling, debug

    async def narrate_action(
        self,
        messages: Sequence[dict[str, str]],
        action: str,
        ruling: ActionRuling,
    ) -> str:
        return ruling.world_response


def default_identity(name: str = "Player", description: str = "") -> PlayerIdentity:
    return PlayerIdentity(display_name=name, persona=description)
