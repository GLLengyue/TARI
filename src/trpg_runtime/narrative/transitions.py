"""Resolve Story choices without calling an author or writing persistent state."""

from ..story.bundle import BeatChoice, StoryBeat, StoryBundle
from .domain import NarrativeAuthorProposal, NarrativeChoice, NarrativeStatePatch


def resolve_transition(
    bundle: StoryBundle,
    current: StoryBeat,
    player_input: str,
    choice: BeatChoice | None,
) -> NarrativeAuthorProposal:
    """Build authoritative turn fields; narration is filled after generation.

    NarrativeAuthorProposal remains the compatibility/event envelope. Its
    authority fields are constructed here, never taken from model output.
    """
    target = bundle.beat(choice.next_beat_id) if choice else current
    patches = [
        NarrativeStatePatch(
            operation=effect.operation,
            path=effect.path,
            new_value=effect.value,
            reason=effect.reason,
        )
        for effect in (choice.effects if choice else [])
    ]
    if player_input.strip():
        patches.append(
            NarrativeStatePatch(
                operation="set",
                path="variables.last_input",
                new_value=player_input.strip(),
                reason="record the player's latest intent",
            )
        )
    if choice:
        patches.append(
            NarrativeStatePatch(
                operation="set",
                path="variables.last_choice",
                new_value=choice.choice_id,
                reason="record the selected story exit",
            )
        )
    return NarrativeAuthorProposal(
        narrative="",
        narrative_beat_id=target.beat_id,
        next_beat_id=target.beat_id,
        advance_beat=choice is not None,
        choices=[NarrativeChoice.from_spec(item) for item in target.choices],
        state_patches=patches,
        revealed_fact_ids=list(choice.reveal_fact_ids) if choice else [],
        source_refs=list(target.source_refs),
        ended=target.terminal,
    )
