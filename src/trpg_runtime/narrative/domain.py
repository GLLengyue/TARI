from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..story.bundle import BeatChoice


class CanonPolicy(StrEnum):
    STRICT = "strict"
    GUIDED = "guided"
    SANDBOX = "sandbox"


class PlayerIdentity(BaseModel):
    identity_id: str = "player"
    display_name: str
    identity_type: Literal["embody", "possess", "visitor", "replacement"] = "visitor"
    persona: str = ""
    host_character: str | None = None
    inherited_abilities: list[str] = Field(default_factory=list)
    inherited_relationships: list[str] = Field(default_factory=list)
    player_knowledge: list[str] = Field(default_factory=list)
    character_knowledge: list[str] = Field(default_factory=list)
    memory_policy: Literal["full", "partial", "none"] = "full"


class NarrativeChoice(BaseModel):
    choice_id: str
    text: str
    risk: Literal["low", "medium", "high"] = "medium"
    next_beat_id: str

    @classmethod
    def from_spec(cls, choice: BeatChoice) -> "NarrativeChoice":
        return cls(
            choice_id=choice.choice_id,
            text=choice.text,
            risk=choice.risk,
            next_beat_id=choice.next_beat_id,
        )


class NarrativeStatePatch(BaseModel):
    operation: Literal["set", "add", "remove", "increment"]
    path: str
    new_value: Any | None = None
    old_value: Any | None = None
    reason: str = ""
    proposed_by: str = "author"


class NarrativeInput(BaseModel):
    text: str = ""
    choice_id: str | None = None
    input_mode: Literal["choice", "freeform", "continue"] = "freeform"


class NarrativeAuthorProposal(BaseModel):
    narrative: str
    narrative_beat_id: str
    next_beat_id: str
    advance_beat: bool = True
    choices: list[NarrativeChoice] = Field(default_factory=list)
    state_patches: list[NarrativeStatePatch] = Field(default_factory=list)
    revealed_fact_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    ended: bool = False
    debug: dict[str, Any] = Field(default_factory=dict)


class StorySessionState(BaseModel):
    session_id: str
    story_id: str
    title: str
    branch_id: str = "main"
    parent_branch_id: str | None = None
    turn_number: int = 0
    version: int = 0
    seed: int = 0
    locale: str = "en"
    canon_policy: CanonPolicy = CanonPolicy.GUIDED
    current_beat_id: str
    player_identity: PlayerIdentity
    variables: dict[str, Any] = Field(default_factory=dict)
    relationship_values: dict[str, int] = Field(default_factory=dict)
    revealed_fact_ids: set[str] = Field(default_factory=set)
    completed_beat_ids: list[str] = Field(default_factory=list)
    available_choices: list[NarrativeChoice] = Field(default_factory=list)
    last_narrative: str = ""
    status: Literal["active", "completed", "paused"] = "active"


class NarrativeTurnResult(BaseModel):
    session_id: str
    story_id: str
    branch_id: str
    turn_number: int
    player_input: str
    input_mode: Literal["choice", "freeform", "continue"]
    choice_id: str | None = None
    narrative: str
    narrative_beat_id: str
    current_beat_id: str
    choices: list[NarrativeChoice] = Field(default_factory=list)
    revealed_fact_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    ended: bool = False
    debug: dict[str, Any] = Field(default_factory=dict)
