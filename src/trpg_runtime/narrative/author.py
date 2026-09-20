from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ..story.bundle import BeatChoice, StoryBeat, StoryBundle
from .domain import (
    NarrativeAuthorProposal,
    NarrativeDraft,
    PlayerIdentity,
    StorySessionState,
)
from .transitions import resolve_transition


class NarrativeAuthor(ABC):
    """Author contract for Story Mode.

    New authors return NarrativeDraft (prose and diagnostics only). Legacy
    proposals are checked against the runtime-resolved transition.
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

    async def aclose(self) -> None:
        """Release provider resources. Authors without a client may ignore this."""
        return None


class FakeNarrativeAuthor(NarrativeAuthor):
    """Offline author used by the vertical slice and regression tests."""

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


def default_identity(name: str = "Player", description: str = "") -> PlayerIdentity:
    return PlayerIdentity(display_name=name, persona=description)
