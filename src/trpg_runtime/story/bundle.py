from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class FactVisibility(StrEnum):
    PUBLIC = "public"
    PLAYER = "player"
    CHARACTER = "character"
    AUTHOR_ONLY = "author_only"


class SourceManifest(BaseModel):
    kind: str = "handcrafted"
    label: str = ""
    sha256: str | None = None
    source_refs: list[str] = Field(default_factory=list)


class SourceEvidence(BaseModel):
    ref_id: str
    label: str = ""
    location: str = ""
    excerpt: str = ""


class CanonFact(BaseModel):
    fact_id: str
    content: str
    source_refs: list[str] = Field(default_factory=list)
    visibility: FactVisibility = FactVisibility.PUBLIC
    known_by: list[str] = Field(default_factory=list)
    status: Literal["canonical", "variant", "deprecated"] = "canonical"


class StoryEntity(BaseModel):
    entity_id: str
    name: str
    kind: str = "character"
    description: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class Relationship(BaseModel):
    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    label: str
    value: int = 0
    description: str = ""


class PlotArc(BaseModel):
    arc_id: str
    title: str
    summary: str = ""
    beat_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class StateEffect(BaseModel):
    """A deterministic effect attached to a compiled choice.

    Effects are private bundle metadata. They are converted into a proposal by
    an author and are validated again by the runtime before being committed.
    """

    operation: Literal["set", "add", "remove", "increment"]
    path: str
    value: Any | None = None
    reason: str = "choice effect"


class BeatChoice(BaseModel):
    choice_id: str
    text: str
    risk: Literal["low", "medium", "high"] = "medium"
    next_beat_id: str
    narrative_hint: str = ""
    effects: list[StateEffect] = Field(default_factory=list)
    reveal_fact_ids: list[str] = Field(default_factory=list)


class StoryBeat(BaseModel):
    beat_id: str
    arc_id: str
    title: str
    location: str = ""
    present_entities: list[str] = Field(default_factory=list)
    dramatic_goal: str
    pressure: str = ""
    available_clues: list[str] = Field(default_factory=list)
    narrative: str
    source_refs: list[str] = Field(default_factory=list)
    decision_required: bool = True
    choices: list[BeatChoice] = Field(default_factory=list)
    terminal: bool = False


class StyleProfile(BaseModel):
    language: str = "en"
    point_of_view: str = "third_person_limited"
    tone: str = "direct"
    constraints: list[str] = Field(default_factory=list)


class StoryBundle(BaseModel):
    """An immutable, portable story package consumed by Story Mode."""

    schema_version: int = 1
    story_id: str
    title: str
    locale: str = "en"
    opening: str
    source: SourceManifest = Field(default_factory=SourceManifest)
    evidence: list[SourceEvidence] = Field(default_factory=list)
    entities: list[StoryEntity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    canon_facts: list[CanonFact] = Field(default_factory=list)
    plot_arcs: list[PlotArc] = Field(default_factory=list)
    story_beats: list[StoryBeat]
    style_profile: StyleProfile = Field(default_factory=StyleProfile)
    optional_rules: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> "StoryBundle":
        if not self.story_beats:
            raise ValueError("story_beats must contain at least one beat")

        entity_ids = {item.entity_id for item in self.entities}
        fact_ids = {item.fact_id for item in self.canon_facts}
        arc_ids = {item.arc_id for item in self.plot_arcs}
        beat_ids = {item.beat_id for item in self.story_beats}

        if len(entity_ids) != len(self.entities):
            raise ValueError("entities must have unique entity_id values")
        if len(beat_ids) != len(self.story_beats):
            raise ValueError("story_beats must have unique beat_id values")
        if len({item.fact_id for item in self.canon_facts}) != len(self.canon_facts):
            raise ValueError("canon_facts must have unique fact_id values")
        if len(arc_ids) != len(self.plot_arcs):
            raise ValueError("plot_arcs must have unique arc_id values")
        for relationship in self.relationships:
            if relationship.source_entity_id not in entity_ids:
                raise ValueError(
                    f"relationship {relationship.relationship_id!r} references unknown "
                    f"source entity {relationship.source_entity_id!r}"
                )
            if relationship.target_entity_id not in entity_ids:
                raise ValueError(
                    f"relationship {relationship.relationship_id!r} references unknown "
                    f"target entity {relationship.target_entity_id!r}"
                )
        for arc in self.plot_arcs:
            unknown_beats = set(arc.beat_ids) - beat_ids
            if unknown_beats:
                raise ValueError(
                    f"arc {arc.arc_id!r} references unknown beats: {sorted(unknown_beats)}"
                )

        for beat in self.story_beats:
            if beat.arc_id not in arc_ids and self.plot_arcs:
                raise ValueError(f"beat {beat.beat_id!r} references unknown arc {beat.arc_id!r}")
            unknown_entities = set(beat.present_entities) - entity_ids
            if unknown_entities:
                raise ValueError(
                    f"beat {beat.beat_id!r} references unknown entities: {sorted(unknown_entities)}"
                )
            unknown_facts = set(beat.available_clues) - fact_ids
            if unknown_facts:
                raise ValueError(
                    f"beat {beat.beat_id!r} references unknown clues: {sorted(unknown_facts)}"
                )
            choice_ids = [choice.choice_id for choice in beat.choices]
            if len(choice_ids) != len(set(choice_ids)):
                raise ValueError(f"beat {beat.beat_id!r} has duplicate choice_id values")
            for choice in beat.choices:
                if choice.next_beat_id not in beat_ids:
                    raise ValueError(
                        f"choice {choice.choice_id!r} references unknown beat "
                        f"{choice.next_beat_id!r}"
                    )
                unknown_reveals = set(choice.reveal_fact_ids) - fact_ids
                if unknown_reveals:
                    raise ValueError(
                        f"choice {choice.choice_id!r} reveals unknown facts: {sorted(unknown_reveals)}"
                    )

        if self.story_beats[0].terminal:
            raise ValueError("the first story beat cannot be terminal")
        return self

    @property
    def first_beat(self) -> StoryBeat:
        return self.story_beats[0]

    def beat(self, beat_id: str) -> StoryBeat:
        for beat in self.story_beats:
            if beat.beat_id == beat_id:
                return beat
        raise KeyError(f"unknown story beat: {beat_id}")

    def fact(self, fact_id: str) -> CanonFact:
        for fact in self.canon_facts:
            if fact.fact_id == fact_id:
                return fact
        raise KeyError(f"unknown canon fact: {fact_id}")

    def entity(self, entity_id: str) -> StoryEntity:
        for entity in self.entities:
            if entity.entity_id == entity_id:
                return entity
        raise KeyError(f"unknown story entity: {entity_id}")


def load_bundle(path: str | Path) -> StoryBundle:
    """Load and validate a JSON or YAML story bundle."""
    bundle_path = Path(path)
    if not bundle_path.is_file():
        raise FileNotFoundError(f"story bundle not found: {bundle_path}")
    text = bundle_path.read_text(encoding="utf-8")
    if bundle_path.suffix.lower() == ".json":
        document = json.loads(text)
    else:
        document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ValueError("story bundle must be a JSON/YAML object")
    return StoryBundle.model_validate(document)


def write_bundle(path: str | Path, bundle: StoryBundle) -> None:
    """Write a bundle using the format implied by the destination suffix."""
    bundle_path = Path(path)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    payload = bundle.model_dump(mode="python")
    if bundle_path.suffix.lower() == ".json":
        bundle_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    else:
        bundle_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
