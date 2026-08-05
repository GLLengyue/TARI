from pathlib import Path

import yaml

from .domain import (
    ActorState,
    CampaignRules,
    CampaignState,
    PlayerState,
    SceneState,
    SpotlightOwner,
    SpotlightToken,
    StoryFramework,
)
from .i18n import DEFAULT_LOCALE


def load_scenario_doc(
    d: dict,
    campaign_id: str | None = None,
    seed: int | None = None,
    lang: str | None = None,
):
    """Build a campaign state from a parsed scenario document.

    New-style documents carry a ``localizations`` section keyed by locale
    code; old-style single-locale documents are treated as English.  An
    optional root ``rules`` block configures the campaign's ruleset.
    """
    if "localizations" in d:
        locale = lang or d.get("default_locale", DEFAULT_LOCALE)
        if locale not in d["localizations"]:
            raise ValueError(
                f"locale {locale!r} is not available in this scenario; "
                f"available locales: {sorted(d['localizations'])}"
            )
        content = d["localizations"][locale]
    else:
        locale = lang or DEFAULT_LOCALE
        if locale != DEFAULT_LOCALE:
            raise ValueError(
                f"scenario has no localizations section; only "
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
        rules=CampaignRules(**(d.get("rules") or {})),
        spotlight=SpotlightToken(
            owner_type=SpotlightOwner.PLAYER,
            owner_id=content["player"]["player_id"],
            scopes={"own_action"},
            granted_at_turn=0,
            reason="campaign start",
        ),
    )


def load_scenario(
    path: str | Path,
    campaign_id: str | None = None,
    seed: int | None = None,
    lang: str | None = None,
):
    """Load a scenario file into a campaign state."""
    with Path(path).open(encoding="utf-8") as f:
        d = yaml.safe_load(f)
    return load_scenario_doc(d, campaign_id=campaign_id, seed=seed, lang=lang)
