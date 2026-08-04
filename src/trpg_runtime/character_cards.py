from __future__ import annotations

import base64
import json
import random
import re
import struct
import zlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .domain import ActorState, KnowledgeItem
from .i18n import DEFAULT_LOCALE


class SidecarScene(BaseModel):
    """GM-controlled scene overrides for an imported card."""

    scene_id: str | None = None
    title: str | None = None
    location: str | None = None
    public_facts: list[str] = Field(default_factory=list)
    hidden_facts: list[str] = Field(default_factory=list)


class CardSidecar(BaseModel):
    """GM-only data that must not live inside the public character card.

    Secrets, goals, attributes, and scene facts belong here; the card itself
    stays a public, shareable asset.
    """

    actor_id: str | None = None
    name: str | None = None
    description: str | None = None
    location: str | None = None
    goals: list[str] = Field(default_factory=list)
    knowledge: list[KnowledgeItem] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    locale: str | None = None
    opening: str | None = None
    scene: SidecarScene | None = None


def _png_text_chunks(path: Path) -> dict[str, str]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG file: {path}")
    pos = 8
    out: dict[str, str] = {}
    while pos + 12 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        if ctype == b"tEXt":
            key, _, value = chunk.decode("utf-8", "replace").partition("\x00")
            out[key] = value
        elif ctype == b"iTXt":
            parts = chunk.split(b"\x00", 3)
            if len(parts) == 4:
                key = parts[0].decode("utf-8", "replace")
                compressed = parts[2] == b"\x01"
                raw = parts[3]
                if compressed:
                    raw = zlib.decompress(raw)
                out[key] = raw.decode("utf-8", "replace")
        pos += 12 + length
    return out


def _decode_blob(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    text = raw
    try:
        blob = base64.b64decode(raw, validate=False)
        if blob[:2] in (b"\x78\x9c", b"\x78\x01", b"\x78\xda"):
            blob = zlib.decompress(blob)
        text = blob.decode("utf-8")
    except Exception:
        text = raw
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("character card blob is not valid JSON") from exc
    if not isinstance(doc, dict):
        raise ValueError("character card blob is not a JSON object")
    return doc


def parse_card(path: str | Path) -> dict[str, Any]:
    """Parse a Character Card V2/V3 from a PNG (embedded) or plain JSON file.

    Returns the card ``data`` object (the ``data`` field when wrapped by the
    spec envelope, or the document itself for bare cards).
    """
    p = Path(path)
    doc: Any
    if p.suffix.lower() == ".png":
        chunks = _png_text_chunks(p)
        blob = chunks.get("ccv3") or chunks.get("chara")
        if blob is None:
            raise ValueError(f"no character card chunk (chara/ccv3) found in {p}")
        doc = _decode_blob(blob)
    else:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError(f"not a character card JSON object: {p}")
    data = doc.get("data")
    data = data if isinstance(data, dict) else doc
    if "name" not in data:
        raise ValueError(f"not a character card: missing 'name' in {p}")
    return data


def _slugify(name: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", name.strip()).strip("_").lower()
    return slug or "actor"


def detect_locale(*texts: str) -> str:
    """Heuristic: if a meaningful share of letters is CJK, prefer Chinese."""
    joined = " ".join(t for t in texts if t)
    if not joined:
        return DEFAULT_LOCALE
    cjk = sum(1 for ch in joined if "\u4e00" <= ch <= "\u9fff")
    letters = sum(1 for ch in joined if ch.isalpha())
    return "zh" if letters and cjk / letters > 0.3 else DEFAULT_LOCALE


def _lorebook_knowledge(card: dict[str, Any]) -> list[KnowledgeItem]:
    book = card.get("character_book")
    if not isinstance(book, dict):
        return []
    entries = book.get("entries", {})
    if not isinstance(entries, dict):
        return []
    items: list[KnowledgeItem] = []
    for uid, entry in entries.items():
        if not isinstance(entry, dict) or not entry.get("content"):
            continue
        items.append(
            KnowledgeItem(
                fact_id=f"lore_{entry.get('uid', uid)}",
                content=str(entry["content"]).strip(),
                confidence=1.0 if entry.get("constant") else 0.8,
                source=f"character_book:{book.get('name', 'unknown')}",
            )
        )
    return items


def _knowledge_from_dicts(items: Any) -> list[KnowledgeItem]:
    out: list[KnowledgeItem] = []
    if not isinstance(items, list):
        return out
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        out.append(
            KnowledgeItem(
                fact_id=str(item.get("fact_id") or f"ext_{index}"),
                content=str(item.get("content", "")),
                confidence=float(item.get("confidence", 0.8)),
                source=str(item.get("source") or "card_extensions"),
            )
        )
    return out


def _tari_extensions(card: dict[str, Any]) -> dict[str, Any]:
    ext = card.get("extensions")
    return ext.get("tari", {}) if isinstance(ext, dict) else {}


def _card_data(card: dict[str, Any]) -> dict[str, Any]:
    """Accept either the spec envelope ({spec, data}) or a bare card data dict."""
    data = card.get("data")
    return data if isinstance(data, dict) else card


def build_actor(card: dict[str, Any], sidecar: CardSidecar | None = None) -> ActorState:
    """Map a character card (plus optional GM sidecar) onto an ActorState."""
    card = _card_data(card)
    s = sidecar
    name = (s.name if s and s.name else None) or card.get("name") or "Unknown"
    actor_id = (s.actor_id if s and s.actor_id else None) or _slugify(str(name))

    if s and s.description:
        description = s.description
    else:
        parts = []
        if card.get("description"):
            parts.append(str(card["description"]).strip())
        if card.get("personality"):
            parts.append(f"Personality: {str(card['personality']).strip()}")
        description = "\n\n".join(parts) or str(name)

    goals = list(s.goals) if s else []
    if not goals:
        goals = [str(x) for x in _tari_extensions(card).get("goals", [])]

    knowledge = list(s.knowledge) if s else []
    if not knowledge:
        knowledge = _lorebook_knowledge(card)
    knowledge.extend(_knowledge_from_dicts(_tari_extensions(card).get("knowledge", [])))

    secrets = list(s.secrets) if s else []

    attributes: dict[str, Any] = {}
    attributes.update(_tari_extensions(card).get("attributes", {}))
    if s and s.attributes:
        attributes.update(s.attributes)
    card_meta = {
        key: card[key]
        for key in (
            "tags",
            "creator",
            "creator_notes",
            "character_version",
            "system_prompt",
            "post_history_instructions",
            "scenario",
            "personality",
            "first_mes",
            "mes_example",
            "alternate_greetings",
        )
        if card.get(key) is not None
    }
    if card_meta:
        attributes["card"] = card_meta

    location = (s.location if s and s.location else None) or str(
        _tari_extensions(card).get("location") or ""
    )
    return ActorState(
        actor_id=actor_id,
        name=name,
        description=description,
        location=location,
        goals=goals,
        knowledge=knowledge,
        secrets=secrets,
        attributes=attributes,
    )


def generate_scenario(
    actor: ActorState,
    card: dict[str, Any],
    sidecar: CardSidecar | None = None,
    *,
    campaign_id: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Build a TARI scenario document (localized format) from an imported card."""
    card = _card_data(card)
    s = sidecar
    locale = (s.locale if s and s.locale else None) or detect_locale(
        actor.description, str(card.get("first_mes") or "")
    )
    opening = (
        (s.opening if s and s.opening else None)
        or card.get("first_mes")
        or (f"The story begins with {actor.name}.")
    )
    scene = s.scene if s else None
    location = (scene.location if scene and scene.location else None) or actor.location or ""
    scene_data = {
        "scene_id": scene.scene_id if scene and scene.scene_id else "imported-scene",
        "title": scene.title if scene and scene.title else f"{actor.name}'s Scene",
        "location": location,
        "public_facts": list(scene.public_facts) if scene else [],
        "hidden_facts": list(scene.hidden_facts) if scene else [],
    }
    actor_data = actor.model_dump(mode="python")
    # The scenario format derives actor.location from scene.location.
    actor_data.pop("location", None)
    content = {
        "title": actor.name,
        "opening": opening,
        "scene": scene_data,
        "player": {
            "player_id": "player",
            "name": "Player",
            "description": "The player character in this story.",
        },
        "actor": actor_data,
        "story_framework": {
            "premise": f"Play out a story with {actor.name}.",
            "required_beats": [],
            "optional_beats": [],
            "forbidden_revelations": [],
            "possible_endings": [],
        },
    }
    return {
        "campaign_id": campaign_id or _slugify(str(actor.name)),
        "seed": seed if seed is not None else random.randrange(1_000_000),
        "default_locale": locale,
        "localizations": {locale: content},
    }


def write_scenario_yaml(path: str | Path, scenario: dict[str, Any]) -> None:
    with Path(path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(scenario, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
