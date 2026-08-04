from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Outcome(StrEnum):
    FULL_SUCCESS = "full_success"
    SUCCESS_WITH_COST = "success_with_cost"
    FAILURE = "failure"


class SpotlightOwner(StrEnum):
    PLAYER = "player"
    GM = "gm"
    ACTOR = "actor"


class SpotlightToken(BaseModel):
    token_id: UUID = Field(default_factory=uuid4)
    owner_type: SpotlightOwner
    owner_id: str
    scopes: set[str]
    granted_at_turn: int
    expires_after_outputs: int = 1
    reason: str


class RollResult(BaseModel):
    check_id: UUID
    dice: Literal["2d6"] = "2d6"
    rolls: tuple[int, int]
    total: int
    outcome: Outcome


class CheckRequest(BaseModel):
    check_id: UUID = Field(default_factory=uuid4)
    actor_id: str
    move: str
    reason: str
    stakes_on_full_success: str
    stakes_on_success_with_cost: str
    stakes_on_failure: str
    visibility: Literal["public", "hidden"] = "public"


class StatePatch(BaseModel):
    operation: Literal["set", "add", "remove", "increment"]
    path: str
    old_value: Any | None = None
    new_value: Any | None = None
    reason: str
    proposed_by: str


class SpotlightGrant(BaseModel):
    owner_type: SpotlightOwner
    owner_id: str
    scopes: set[str]
    reason: str


class GMDecision(BaseModel):
    reasoning_summary: str
    public_narration: str | None = None
    check_request: CheckRequest | None = None
    proposed_state_patches: list[StatePatch] = Field(default_factory=list)
    actor_observations: dict[str, list[str]] = Field(default_factory=dict)
    next_spotlight: SpotlightGrant
    scene_status: Literal["continue", "complete", "blocked"] = "continue"


class ActorTurn(BaseModel):
    speech: str | None = None
    action: str | None = None
    private_thought: str | None = None
    intent: str
    requested_check: CheckRequest | None = None
    factual_claims: list[str] = Field(default_factory=list)


class AuditViolation(BaseModel):
    code: str
    message: str


class AuditResult(BaseModel):
    accepted: bool
    violations: list[AuditViolation] = Field(default_factory=list)
    retry_instruction: str | None = None


class KnowledgeItem(BaseModel):
    fact_id: str
    content: str
    confidence: float = Field(ge=0, le=1)
    source: str


class PlayerState(BaseModel):
    player_id: str
    name: str
    description: str


class ActorState(BaseModel):
    actor_id: str
    name: str
    description: str
    location: str
    goals: list[str] = Field(default_factory=list)
    knowledge: list[KnowledgeItem] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class SceneState(BaseModel):
    scene_id: str
    title: str
    location: str
    public_facts: list[str] = Field(default_factory=list)
    hidden_facts: list[str] = Field(default_factory=list)


class StoryFramework(BaseModel):
    premise: str
    required_beats: list[str] = Field(default_factory=list)
    optional_beats: list[str] = Field(default_factory=list)
    forbidden_revelations: list[str] = Field(default_factory=list)
    possible_endings: list[str] = Field(default_factory=list)


class CampaignState(BaseModel):
    campaign_id: str
    title: str
    opening: str
    turn_number: int = 0
    seed: int
    locale: str = "en"
    scene: SceneState
    player: PlayerState
    actors: dict[str, ActorState]
    story_framework: StoryFramework
    spotlight: SpotlightToken
    status: Literal["active", "paused", "completed"] = "active"
    version: int = 0


class GMView(BaseModel):
    state: CampaignState
    recent_public_events: list[str]


class ActorView(BaseModel):
    campaign_id: str
    turn_number: int
    actor: ActorState
    public_facts: list[str]
    observations: list[str]
    recent_public_events: list[str]
    spotlight: SpotlightToken


class AgentUsage(BaseModel):
    agent_id: str
    model_id: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int


class TurnResult(BaseModel):
    campaign_id: str
    turn_number: int
    player_input: str
    roll: RollResult | None = None
    gm_narration: str | None = None
    actor_speech: str | None = None
    actor_action: str | None = None
    debug: dict[str, Any] = Field(default_factory=dict)
