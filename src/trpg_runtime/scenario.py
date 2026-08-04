from pathlib import Path

import yaml

from .domain import (
    ActorState,
    CampaignState,
    PlayerState,
    SceneState,
    SpotlightOwner,
    SpotlightToken,
    StoryFramework,
)


def load_scenario(path: str | Path, campaign_id: str | None = None, seed: int | None = None):
    with Path(path).open(encoding="utf-8") as f:
        d = yaml.safe_load(f)
    actor = ActorState(
        **d["actor"],
        location=d["scene"]["location"],
    )
    return CampaignState(
        campaign_id=campaign_id or d["campaign_id"],
        title=d["title"],
        opening=d["opening"],
        seed=seed if seed is not None else d["seed"],
        scene=SceneState(**d["scene"]),
        player=PlayerState(**d["player"]),
        actors={actor.actor_id: actor},
        story_framework=StoryFramework(**d["story_framework"]),
        spotlight=SpotlightToken(
            owner_type=SpotlightOwner.PLAYER,
            owner_id=d["player"]["player_id"],
            scopes={"own_action"},
            granted_at_turn=0,
            reason="campaign start",
        ),
    )
