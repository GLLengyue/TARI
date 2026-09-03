from __future__ import annotations

import random
from typing import Any

import yaml
from pydantic import BaseModel

from .character_cards import build_actor, generate_scenario
from .domain import CampaignRules, CampaignState
from .gm_docs import effective_rules_text
from .lorebook import apply_world_info
from .resource_library import ResourceLibrary
from .scenario import load_scenario_doc


class ComposeRequest(BaseModel):
    scenario_id: str | None = None
    card_id: str | None = None
    world_id: str | None = None
    ruleset_id: str = "pbta-minimal"
    custom_rules: str = ""
    lang: str = "en"
    campaign_id: str | None = None
    seed: int | None = None
    player_name: str | None = None
    player_description: str | None = None


def _default_scenario(lang: str, campaign_id: str | None, seed: int | None) -> dict[str, Any]:
    is_zh = lang == "zh"
    opening = (
        "故事从一片尚未写下的空白开始。你睁开眼睛，世界正在等待你的第一个行动。"
        if is_zh
        else (
            "The story begins on a blank page. You open your eyes, "
            "and the world waits for your first move."
        )
    )
    title = "新战役" if is_zh else "New Campaign"
    actor_name = "世界" if is_zh else "The World"
    actor_description = (
        "一个中立的世界执行者：负责回应玩家的行动，但不替玩家做决定。"
        if is_zh
        else (
            "A neutral world executor: it responds to the player's actions "
            "but never decides for the player."
        )
    )
    premise = "自由探索一段由 GM 主持的故事。" if is_zh else "Explore a GM-run story freely."
    return {
        "campaign_id": campaign_id or f"campaign-{random.randrange(1_000_000)}",
        "seed": seed if seed is not None else random.randrange(1_000_000),
        "default_locale": lang,
        "localizations": {
            lang: {
                "title": title,
                "opening": opening,
                "scene": {
                    "scene_id": "blank",
                    "title": title,
                    "location": "",
                    "public_facts": [],
                    "hidden_facts": [],
                },
                "player": {
                    "player_id": "player",
                    "name": "玩家" if is_zh else "Player",
                    "description": "本场故事的玩家角色。"
                    if is_zh
                    else "The player character in this story.",
                },
                "actor": {
                    "actor_id": "world",
                    "name": actor_name,
                    "description": actor_description,
                    "goals": [],
                    "knowledge": [],
                    "secrets": [],
                    "attributes": {},
                },
                "story_framework": {
                    "premise": premise,
                    "required_beats": [],
                    "optional_beats": [],
                    "forbidden_revelations": [],
                    "possible_endings": [],
                },
            }
        },
    }


def compose_state(library: ResourceLibrary, req: ComposeRequest) -> CampaignState:
    """Compose a campaign state from scenario / card / world / rules selections."""
    scenario_doc: dict[str, Any] | None = None
    if req.scenario_id:
        res = library.get("scenarios", req.scenario_id)
        if res is None:
            raise ValueError(f"unknown scenario: {req.scenario_id}")
        scenario_doc = yaml.safe_load(res.path.read_text(encoding="utf-8"))

    if req.card_id:
        res = library.get("cards", req.card_id)
        if res is None:
            raise ValueError(f"unknown card: {req.card_id}")
        card = library.load_card(res)
        actor = build_actor(card)
        if scenario_doc is None:
            scenario_doc = generate_scenario(
                actor, card, campaign_id=req.campaign_id, seed=req.seed
            )
            if req.lang not in scenario_doc["localizations"]:
                detected = next(iter(scenario_doc["localizations"]))
                scenario_doc["localizations"][req.lang] = scenario_doc["localizations"][detected]
            scenario_doc["default_locale"] = req.lang
        else:
            actor_data = actor.model_dump(mode="python")
            actor_data.pop("location", None)
            if "localizations" in scenario_doc:
                for loc in scenario_doc["localizations"].values():
                    if isinstance(loc, dict):
                        loc["actor"] = actor_data
            else:
                scenario_doc["actor"] = actor_data

    if scenario_doc is None:
        scenario_doc = _default_scenario(req.lang, req.campaign_id, req.seed)

    state = load_scenario_doc(
        scenario_doc, campaign_id=req.campaign_id, seed=req.seed, lang=req.lang
    )

    if req.world_id:
        res = library.get("worlds", req.world_id)
        if res is None:
            raise ValueError(f"unknown world: {req.world_id}")
        state = apply_world_info(state, library.load_world_book(res))

    state = state.model_copy(
        update={
            "rules": CampaignRules(ruleset_id=req.ruleset_id, custom_rules=req.custom_rules)
        }
    )
    if req.player_name or req.player_description:
        player = state.player
        state = state.model_copy(
            update={
                "player": player.model_copy(
                    update={
                        "name": req.player_name or player.name,
                        "description": req.player_description or player.description,
                    }
                )
            }
        )
    return state


def build_preview(library: ResourceLibrary, req: ComposeRequest) -> dict[str, Any]:
    """Build the composition preview without creating a campaign."""
    state = compose_state(library, req)
    actor = next(iter(state.actors.values()))
    card_res = library.get("cards", req.card_id) if req.card_id else None
    world_res = library.get("worlds", req.world_id) if req.world_id else None
    avatar_url = (
        f"/api/resources/avatar?resource_id={card_res.id}"
        if card_res is not None and card_res.meta.get("avatar")
        else (
            f"/api/resources/avatar?resource_id={world_res.id}"
            if world_res is not None and world_res.meta.get("avatar")
            else None
        )
    )
    return {
        "title": state.title,
        "locale": state.locale,
        "opening": state.opening,
        "scene": {
            "title": state.scene.title,
            "location": state.scene.location,
            "public_facts": state.scene.public_facts,
            "hidden_count": len(state.scene.hidden_facts),
        },
        "player": state.player.model_dump(mode="json"),
        "actor": {
            "actor_id": actor.actor_id,
            "name": actor.name,
            "description": actor.description,
            "goals": actor.goals,
            "avatar_url": avatar_url,
        },
        "story_framework": state.story_framework.model_dump(mode="json"),
        "rules": {
            "ruleset_id": state.rules.ruleset_id,
            "custom_rules": state.rules.custom_rules,
            "effective_text": effective_rules_text(state),
            "dice_note": "v1 dice engine: fixed 2d6 PbtA bands",
        },
        "warnings": [],
    }


def create_campaign(store, state: CampaignState) -> CampaignState:
    """Persist a new campaign (append creation event + snapshot)."""
    store.append(
        state.campaign_id, 0, "campaign_created", {"title": state.title, "seed": state.seed}
    )
    store.save_snapshot(state)
    return state
