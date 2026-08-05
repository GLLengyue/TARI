from __future__ import annotations

import json

from .domain import ActorView, CampaignState, GMView


def build_gm_view(state: CampaignState, recent: list[str]) -> GMView:
    return GMView(state=state, recent_public_events=recent[-12:])


def compact_gm_view(view: GMView) -> str:
    """Serialize the GM's prompt view without presentation-heavy card blobs.

    The runtime keeps the full campaign state for tool dependencies, but the
    prompt itself only needs the adjudication-relevant fields: scene facts,
    actor profiles (without ST card attributes such as HTML greetings or
    example dialogue), player, framework, spotlight, and recent events.
    Full card details remain available on demand through the GM tools.
    """
    state = view.state
    compact = {
        "campaign_id": state.campaign_id,
        "title": state.title,
        "turn_number": state.turn_number,
        "locale": state.locale,
        "scene": state.scene.model_dump(mode="json"),
        "player": state.player.model_dump(mode="json"),
        "actors": {
            actor_id: actor.model_dump(mode="json", exclude={"attributes"})
            for actor_id, actor in state.actors.items()
        },
        "story_framework": state.story_framework.model_dump(mode="json"),
        "spotlight": state.spotlight.model_dump(mode="json"),
        "status": state.status,
        "recent_public_events": view.recent_public_events,
    }
    return json.dumps(compact, ensure_ascii=False, default=str)


def build_actor_view(
    state: CampaignState, actor_id: str, observations: list[str], recent: list[str]
) -> ActorView:
    """Build the fiction-only view for a roleplay agent.

    Contract (the "player agent" is weak by construction):
    - The view contains only fiction-layer information this character may know:
      the character's own ActorState, public scene facts, GM-provided
      observations, recent public events, and the spotlight token.
    - It MUST NOT contain: scene hidden facts, the story framework, other
      actors' states (including their knowledge/secrets), or the player's
      private information. Enforcement is tested in test_projection.py.
    """
    actor = state.actors[actor_id]
    # ActorState intentionally contains only this actor's own knowledge and secrets.
    return ActorView(
        campaign_id=state.campaign_id,
        turn_number=state.turn_number,
        actor=actor,
        public_facts=list(state.scene.public_facts),
        observations=observations,
        recent_public_events=recent[-8:],
        spotlight=state.spotlight,
    )
