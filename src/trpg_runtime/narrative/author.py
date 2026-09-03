from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ..story.bundle import BeatChoice, StoryBeat, StoryBundle
from .domain import (
    NarrativeAuthorProposal,
    NarrativeChoice,
    NarrativeStatePatch,
    PlayerIdentity,
    StorySessionState,
)


class NarrativeAuthor(ABC):
    """Author contract for Story Mode.

    An author may write prose and propose structured effects. The runtime still
    validates the proposal and owns state, branch, and event commits.
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
    ) -> NarrativeAuthorProposal:
        raise NotImplementedError


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
        target = current_beat
        advance = False
        patches: list[NarrativeStatePatch] = []
        revealed: list[str] = []
        narrative_suffix = ""

        if selected_choice is not None:
            target = bundle.beat(selected_choice.next_beat_id)
            advance = True
            narrative_suffix = selected_choice.narrative_hint or (
                f"Your decision echoes through the scene: {selected_choice.text}"
            )
            patches.extend(
                NarrativeStatePatch(
                    operation=effect.operation,
                    path=effect.path,
                    new_value=effect.value,
                    reason=effect.reason,
                    proposed_by="author",
                )
                for effect in selected_choice.effects
            )
            revealed = list(selected_choice.reveal_fact_ids)

        if player_input.strip():
            patches.append(
                NarrativeStatePatch(
                    operation="set",
                    path="variables.last_input",
                    new_value=player_input.strip(),
                    reason="record the player's latest intent",
                    proposed_by="author",
                )
            )
        if selected_choice is not None:
            patches.append(
                NarrativeStatePatch(
                    operation="set",
                    path="variables.last_choice",
                    new_value=selected_choice.choice_id,
                    reason="record the selected story exit",
                    proposed_by="author",
                )
            )

        narrative = target.narrative.strip()
        if narrative_suffix:
            narrative = narrative + "\n\n" + narrative_suffix
        elif player_input.strip():
            narrative = narrative + "\n\nThe story receives your action: " + player_input.strip()

        return NarrativeAuthorProposal(
            narrative=narrative,
            narrative_beat_id=target.beat_id,
            next_beat_id=target.beat_id,
            advance_beat=advance,
            choices=[NarrativeChoice.from_spec(choice) for choice in target.choices],
            state_patches=patches,
            revealed_fact_ids=revealed,
            source_refs=list(target.source_refs),
            ended=target.terminal,
            debug={"author": "fake", "recent_event_count": len(recent_events)},
        )


def default_identity(name: str = "Player", description: str = "") -> PlayerIdentity:
    return PlayerIdentity(display_name=name, persona=description)
