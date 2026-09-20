"""A bounded public writing context, separate from the full story resource."""

from collections.abc import Sequence
from typing import Any

from ..story.bundle import BeatChoice, FactVisibility, StoryBeat, StoryBundle
from .domain import StorySessionState


def build_author_context(
    bundle: StoryBundle,
    state: StorySessionState,
    current: StoryBeat,
    target: StoryBeat,
    player_input: str,
    choice: BeatChoice | None,
    recent_events: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Project only relevant public facts, authorized reveals, and public history.

    Scene source prose remains authored bundle content, not a mechanically
    verified spoiler-free excerpt. Never add full outlines or private events.
    """
    revealed = state.revealed_fact_ids | set(choice.reveal_fact_ids if choice else [])
    relevant = set(current.available_clues) | set(target.available_clues)
    facts = [
        {"fact_id": fact.fact_id, "content": fact.content}
        for fact in bundle.canon_facts
        if fact.visibility != FactVisibility.AUTHOR_ONLY
        and (
            fact.fact_id in revealed
            or (fact.visibility == FactVisibility.PUBLIC and fact.fact_id in relevant)
        )
    ]
    history = []
    for event in recent_events:
        payload = event.get("payload", {})
        if event.get("type") == "story_narrative_emitted":
            history.append({"role": "narrator", "text": str(payload.get("text", ""))})
        elif event.get("type") == "story_player_input_received" and not payload.get("automatic"):
            history.append({"role": "player", "text": str(payload.get("text", ""))})
    identity = state.player_identity
    return {
        "story": {"id": bundle.story_id, "title": bundle.title, "locale": state.locale},
        "style": bundle.style_profile.model_dump(mode="json"),
        "player": {
            "name": identity.display_name,
            "persona": identity.persona,
            "identity_type": identity.identity_type,
            "host_character": identity.host_character,
        },
        "current_scene": {"id": current.beat_id, "title": current.title},
        "target_scene": {
            "id": target.beat_id,
            "title": target.title,
            "location": target.location,
            "dramatic_goal": target.dramatic_goal,
            "source_prose": target.narrative,
            "terminal": target.terminal,
            "boundary": target.boundary.model_dump() if target.boundary else None,
            "awaiting_player_decision": target.decision_required and not target.terminal,
            "pending_directions": [choice.text for choice in target.choices]
            if target.decision_required and not target.terminal
            else [],
        },
        "committed_state": {
            "variables": state.variables,
            "relationships": state.relationship_values,
            "last_narrative": state.last_narrative,
        },
        "visible_facts": facts,
        "player_decisions": [decision.model_dump() for decision in state.decisions[-12:]],
        "recent_public_history": history[-12:],
        "player_direction": player_input,
        "resolved_choice": None
        if choice is None
        else {
            "kind": "decision" if current.decision_required else "continuation",
            "text": choice.text,
            "consequence_hint": choice.narrative_hint,
            "effects": [effect.model_dump(mode="json") for effect in choice.effects],
        },
    }
