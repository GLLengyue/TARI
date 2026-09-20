from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool

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
    def from_spec(cls, choice: BeatChoice) -> NarrativeChoice:
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


class NarrativeDraft(BaseModel):
    """Prose-only author output. The runtime supplies all authoritative fields."""

    narrative: str
    debug: dict[str, Any] = Field(default_factory=dict)


class NarrativeAuthorProposal(NarrativeDraft):
    """Legacy author response, accepted only when it matches the resolved turn."""

    narrative_beat_id: str
    next_beat_id: str
    advance_beat: bool = True
    choices: list[NarrativeChoice] = Field(default_factory=list)
    state_patches: list[NarrativeStatePatch] = Field(default_factory=list)
    revealed_fact_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    ended: bool = False


class ActionRuling(BaseModel):
    """The ruling model's verdict on one freeform action.

    Both branches must carry ``world_response``: the world has to answer an
    impossible action in-story, not with a system error.
    """

    feasible: StrictBool
    reason: str = ""
    world_response: str
    consequences: list[NarrativeStatePatch] = Field(default_factory=list)
    tension_resolved: StrictBool = False
    resolution: str = ""


class FreeformAction(BaseModel):
    turn: int
    player_input: str
    feasible: bool
    reason: str = ""
    world_response: str = ""
    patches: list[NarrativeStatePatch] = Field(default_factory=list)
    tension_resolved: bool = False


class FreeformSegment(BaseModel):
    """One bounded stretch where the player acts freely.

    Entry is a live tension; exit is that tension being resolved -- by the
    player's route, not by a route someone wrote down in advance.
    """

    segment_id: str
    entry_beat_id: str
    tension: str
    stakes: str = ""
    status: Literal["open", "resolved"] = "open"
    actions: list[FreeformAction] = Field(default_factory=list)
    resolution: str = ""


class StoryDecision(BaseModel):
    turn_number: int
    beat_id: str
    choice_id: str
    text: str
    consequence_hint: str = ""


class StorySessionState(BaseModel):
    session_id: str
    story_id: str
    bundle_digest: str | None = None
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
    #: Frozen at session creation. The live ``variables`` change every turn and
    #: must never reach the prompt prefix, or the provider cache never hits.
    initial_variables: dict[str, Any] = Field(default_factory=dict)
    relationship_values: dict[str, int] = Field(default_factory=dict)
    revealed_fact_ids: set[str] = Field(default_factory=set)
    completed_beat_ids: list[str] = Field(default_factory=list)
    decisions: list[StoryDecision] = Field(default_factory=list)
    available_choices: list[NarrativeChoice] = Field(default_factory=list)
    last_narrative: str = ""
    active_segment: FreeformSegment | None = None
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


class ReadingRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    max_scenes: int = Field(default=3, ge=1, le=8)
    choice_id: str | None = None


class ReadingScene(BaseModel):
    turn_number: int
    narrative_beat_id: str
    narrative: str
    source_refs: list[str] = Field(default_factory=list)


class ReadingBatch(BaseModel):
    request_id: str
    session_id: str
    branch_id: str
    stop_reason: Literal["awaiting_choice", "completed", "budget_exhausted"]
    scenes: list[ReadingScene]
    current_beat_id: str
    turn_number: int
    version: int
    choices: list[NarrativeChoice] = Field(default_factory=list)


class ReadingRun(BaseModel):
    """Persistent request identity; scene commits are journaled in turn_results."""

    run_id: str
    request: ReadingRequest
    session_id: str
    branch_id: str
    bundle_digest: str
    start_turn: int
    start_version: int
    result: ReadingBatch | None = None
