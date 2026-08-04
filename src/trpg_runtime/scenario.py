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
from .i18n import DEFAULT_LOCALE


def load_scenario(
    path: str | Path,
    campaign_id: str | None = None,
    seed: int | None = None,
    lang: str | None = None,
):
    """Load a scenario. New-style files carry a ``localizations`` section keyed
    by locale code; old-style single-locale files are treated as English."""
    with Path(path).open(encoding="utf-8") as f:
        d = yaml.safe_load(f)
    if "localizations" in d:
        locale = lang or d.get("default_locale", DEFAULT_LOCALE)
        if locale not in d["localizations"]:
            raise ValueError(
                f"locale {locale!r} is not available in {path}; "
                f"available locales: {sorted(d['localizations'])}"
            )
        content = d["localizations"][locale]
    else:
        locale = lang or DEFAULT_LOCALE
        if locale != DEFAULT_LOCALE:
            raise ValueError(
                f"scenario {path} has no localizations section; only "
                f"{DEFAULT_LOCALE!r} is available"
            )
        content = d
    actor_kwargs = dict(content["actor"])
    # Actor blocks may carry their own location; fall back to the scene location.
    actor_kwargs.setdefault("location", content["scene"]["location"])
    actor = ActorState(**actor_kwargs)
    return CampaignState(
        campaign_id=campaign_id or d["campaign_id"],
        title=content["title"],
        opening=content["opening"],
        seed=seed if seed is not None else d["seed"],
        locale=locale,
        scene=SceneState(**content["scene"]),
        player=PlayerState(**content["player"]),
        actors={actor.actor_id: actor},
        story_framework=StoryFramework(**content["story_framework"]),
        spotlight=SpotlightToken(
            owner_type=SpotlightOwner.PLAYER,
            owner_id=content["player"]["player_id"],
            scopes={"own_action"},
            granted_at_turn=0,
            reason="campaign start",
        ),
    )
