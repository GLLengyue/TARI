from .domain import ActorView, CampaignState, GMView


def build_gm_view(state: CampaignState, recent: list[str]) -> GMView:
    return GMView(state=state, recent_public_events=recent[-12:])


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
