from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

from ..rules import RuleViolation
from ..story.bundle import BeatChoice, FactVisibility, StoryBeat, StoryBundle
from .author import NarrativeAuthor, default_identity
from .domain import (
    CanonPolicy,
    FreeformAction,
    FreeformSegment,
    NarrativeAuthorProposal,
    NarrativeChoice,
    NarrativeInput,
    NarrativeTurnResult,
    PlayerIdentity,
    StoryDecision,
    StorySessionState,
)
from .session_context import build_messages, build_static_prefix
from .storage import StoryConflict, StoryStore
from .transitions import resolve_transition

_ALLOWED_PATCH_ROOTS = ("variables", "relationship_values")


def _normalize_path(path: str) -> str:
    return path.strip().strip("\"'").replace("/", ".").strip(".")


def _apply_state_patches(state: StorySessionState, patches) -> StorySessionState:
    """Apply author effects to a copy, with a deliberately narrow write surface."""
    trial = copy.deepcopy(state.model_dump(mode="python"))
    for patch in patches:
        if patch.proposed_by not in {"author", "fake-author"}:
            raise RuleViolation("only the narrative author may propose story patches")
        path = _normalize_path(patch.path)
        parts = path.split(".")
        if len(parts) != 2 or parts[0] not in _ALLOWED_PATCH_ROOTS or not parts[1]:
            raise RuleViolation(f"forbidden narrative patch path: {path}")
        container = trial[parts[0]]
        key = parts[1]
        existing = container.get(key)
        if patch.old_value is not None and existing != patch.old_value:
            raise RuleViolation(f"old value mismatch at {path}")
        if patch.operation == "set":
            container[key] = patch.new_value
        elif patch.operation == "increment":
            if not isinstance(existing, (int, float)) or not isinstance(
                patch.new_value, (int, float)
            ):
                raise RuleViolation(f"increment target is not numeric at {path}")
            container[key] = existing + patch.new_value
        elif patch.operation == "add":
            if existing is None:
                container[key] = [patch.new_value]
            elif isinstance(existing, list):
                existing.append(patch.new_value)
            else:
                raise RuleViolation(f"add target is not a list at {path}")
        elif patch.operation == "remove":
            if not isinstance(existing, list):
                raise RuleViolation(f"remove target is not a list at {path}")
            try:
                existing.remove(patch.new_value)
            except ValueError as exc:
                raise RuleViolation(f"value is not present at {path}") from exc
    trial["version"] = state.version + 1
    return StorySessionState.model_validate(trial)


class NarrativeOrchestrator:
    """One-call-per-decision interactive narrative runtime."""

    def __init__(
        self,
        store: StoryStore,
        bundle: StoryBundle,
        author: NarrativeAuthor,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.store = store
        self.bundle = bundle
        self.author = author
        self.on_progress = on_progress

    def _progress(self, stage: str, **payload: Any) -> None:
        if self.on_progress is not None:
            self.on_progress(stage, payload)

    async def start_session(
        self,
        identity: PlayerIdentity | None = None,
        *,
        session_id: str | None = None,
        seed: int = 0,
        canon_policy: CanonPolicy = CanonPolicy.GUIDED,
    ) -> StorySessionState:
        session_id = session_id or self.bundle.story_id + "-" + uuid4().hex[:8]
        identity = identity or default_identity()
        first = self.bundle.first_beat
        state = StorySessionState(
            session_id=session_id,
            story_id=self.bundle.story_id,
            bundle_digest=self.bundle.content_digest,
            title=self.bundle.title,
            seed=seed,
            locale=self.bundle.locale,
            canon_policy=canon_policy,
            current_beat_id=first.beat_id,
            player_identity=identity,
            variables={"trust": 0},
            initial_variables={"trust": 0},
            available_choices=[NarrativeChoice.from_spec(choice) for choice in first.choices],
            last_narrative=self.bundle.opening,
        )
        self._progress("session_created", session_id=session_id, beat_id=first.beat_id)
        self.store.create_story_session(state)
        return state

    async def process_turn(
        self,
        state: StorySessionState,
        player_input: str | Mapping[str, Any] | NarrativeInput,
        request_id: str | None = None,
    ) -> tuple[StorySessionState, NarrativeTurnResult]:
        if request_id is not None:
            try:
                cached = self.store.load_story_turn_result(
                    request_id, state.session_id, state.branch_id
                )
            except ValueError as exc:
                raise RuleViolation(str(exc)) from exc
            if cached is not None:
                return self.store.load_story_snapshot(cached.session_id, cached.branch_id), cached

        persisted = self.store.load_story_snapshot(state.session_id, state.branch_id)
        if persisted != state:
            raise StoryConflict("conflicting story turn commit: stale state; reload before writing")
        if state.story_id != self.bundle.story_id or (
            state.bundle_digest is not None and state.bundle_digest != self.bundle.content_digest
        ):
            raise StoryConflict("story bundle changed; restore the session's original bundle")
        if state.status != "active":
            raise RuleViolation("story session is not active")
        if isinstance(player_input, str):
            incoming = NarrativeInput(text=player_input)
        elif isinstance(player_input, Mapping):
            incoming = NarrativeInput.model_validate(player_input)
        elif isinstance(player_input, NarrativeInput):
            incoming = player_input
        else:
            raise TypeError("story input must be a string, mapping, or NarrativeInput")
        current = self.bundle.beat(state.current_beat_id)
        turn = state.turn_number + 1
        tx = self.store.begin_story_turn(
            state.session_id, state.branch_id, turn, expected_version=state.version
        )
        try:
            selected_choice = self._resolve_choice(state, current, incoming)
            text = incoming.text.strip()
            if selected_choice is not None and not text:
                text = selected_choice.text
            if not text and incoming.input_mode != "continue":
                raise RuleViolation("story input cannot be empty")

            self._progress("player_input", turn=turn, choice_id=incoming.choice_id)
            tx.append(
                "story_player_input_received",
                {
                    "text": text,
                    "input_mode": incoming.input_mode,
                    "choice_id": incoming.choice_id,
                    "automatic": selected_choice is not None and not current.decision_required,
                },
            )
            proposal = resolve_transition(self.bundle, current, text, selected_choice)
            # Validate deterministic effects before spending a provider call.
            new_state = _apply_state_patches(state, proposal.state_patches)
            for fact_id in proposal.revealed_fact_ids:
                if self.bundle.fact(fact_id).visibility == FactVisibility.AUTHOR_ONLY:
                    raise RuleViolation(f"author-only fact cannot be revealed: {fact_id}")
            recent = [
                event
                for event in self.store.story_events(state.session_id, state.branch_id)
                if event["type"] in {"story_narrative_emitted", "story_player_input_received"}
            ][-12:]
            self._progress("authoring", turn=turn)
            draft = await self.author.generate(
                self.bundle,
                state,
                current,
                text,
                selected_choice,
                recent,
            )
            if not draft.narrative.strip():
                raise RuleViolation("narrative author returned empty prose")
            if isinstance(draft, NarrativeAuthorProposal):
                self._validate_proposal(state, current, selected_choice, draft)
                self._validate_legacy_transition(draft, proposal)
            proposal.narrative = draft.narrative
            proposal.debug = draft.debug
            new_state = self._commit_proposal(new_state, current, proposal, turn, selected_choice)
            tx.append("story_author_proposal_accepted", proposal.model_dump(mode="json"))
            tx.append(
                "story_narrative_emitted",
                {
                    "beat_id": proposal.narrative_beat_id,
                    "text": proposal.narrative,
                    "source_refs": proposal.source_refs,
                },
            )
            tx.append(
                "story_state_updated",
                {
                    "current_beat_id": new_state.current_beat_id,
                    "completed_beat_ids": new_state.completed_beat_ids,
                    "revealed_fact_ids": sorted(new_state.revealed_fact_ids),
                    "patches": [patch.model_dump(mode="json") for patch in proposal.state_patches],
                    "version": new_state.version,
                    "decisions": [decision.model_dump() for decision in new_state.decisions],
                },
            )
            tx.append(
                "story_turn_completed",
                {"status": new_state.status, "version": new_state.version},
            )
            result = NarrativeTurnResult(
                session_id=new_state.session_id,
                story_id=new_state.story_id,
                branch_id=new_state.branch_id,
                turn_number=turn,
                player_input=text,
                input_mode=incoming.input_mode,
                choice_id=incoming.choice_id,
                narrative=proposal.narrative,
                narrative_beat_id=proposal.narrative_beat_id,
                current_beat_id=new_state.current_beat_id,
                choices=new_state.available_choices,
                revealed_fact_ids=sorted(
                    set(proposal.revealed_fact_ids) & new_state.revealed_fact_ids
                ),
                source_refs=proposal.source_refs,
                ended=new_state.status == "completed",
                debug=proposal.debug,
            )
        except Exception as exc:
            tx.abort(str(exc))
            raise

        tx.commit(new_state, request_id=request_id, result=result)
        self._progress("completed", turn=turn, branch_id=new_state.branch_id)
        return new_state, result

    def fork(self, state: StorySessionState, branch_id: str) -> StorySessionState:
        """Create a child timeline without mutating the parent snapshot."""
        return self.store.create_story_branch(state, branch_id)

    def open_segment(
        self,
        state: StorySessionState,
        *,
        tension: str,
        stakes: str = "",
    ) -> StorySessionState:
        """Enter a freeform segment at a live tension.

        The tension is what the player must resolve; it is written to the event
        log rather than the prompt prefix so the prefix stays byte-identical.
        """
        if state.active_segment is not None and state.active_segment.status == "open":
            raise RuleViolation("a freeform segment is already open")
        tension = tension.strip()
        if not tension:
            raise RuleViolation("a freeform segment needs a tension")

        turn = state.turn_number + 1
        segment = FreeformSegment(
            segment_id=f"segment-{turn:04d}",
            entry_beat_id=state.current_beat_id,
            tension=tension,
            stakes=stakes.strip(),
        )
        trial = state.model_dump(mode="python")
        trial["active_segment"] = segment.model_dump(mode="python")
        trial["available_choices"] = []
        trial["turn_number"] = turn
        trial["version"] = state.version + 1
        new_state = StorySessionState.model_validate(trial)

        tx = self.store.begin_story_turn(
            state.session_id, state.branch_id, turn, expected_version=state.version
        )
        try:
            tx.append(
                "story_segment_opened",
                {
                    "segment_id": segment.segment_id,
                    "entry_beat_id": segment.entry_beat_id,
                    "tension": segment.tension,
                    "stakes": segment.stakes,
                },
            )
            tx.append(
                "story_turn_completed",
                {"status": new_state.status, "version": new_state.version},
            )
        except Exception as exc:
            tx.abort(str(exc))
            raise
        tx.commit(new_state)
        self._progress("segment_opened", turn=turn, segment_id=segment.segment_id)
        return new_state

    def close_segment(
        self,
        state: StorySessionState,
        *,
        resolution: str = "",
    ) -> StorySessionState:
        """Leave the freeform segment and hand control back to the long form."""
        segment = state.active_segment
        if segment is None or segment.status != "open":
            raise RuleViolation("no freeform segment is open")
        current = self.bundle.beat(state.current_beat_id)
        turn = state.turn_number + 1
        text = resolution.strip() or "玩家结束了这一段。"

        trial = state.model_dump(mode="python")
        trial["active_segment"] = None
        trial["turn_number"] = turn
        trial["version"] = state.version + 1
        trial["available_choices"] = [
            NarrativeChoice.from_spec(choice).model_dump() for choice in current.choices
        ]
        new_state = StorySessionState.model_validate(trial)

        tx = self.store.begin_story_turn(
            state.session_id, state.branch_id, turn, expected_version=state.version
        )
        try:
            tx.append(
                "story_segment_resolved",
                {"segment_id": segment.segment_id, "resolution": text},
            )
            tx.append(
                "story_turn_completed",
                {"status": new_state.status, "version": new_state.version},
            )
        except Exception as exc:
            tx.abort(str(exc))
            raise
        tx.commit(new_state)
        self._progress("segment_resolved", turn=turn, segment_id=segment.segment_id)
        return new_state

    async def process_freeform_action(
        self,
        state: StorySessionState,
        action: str,
        request_id: str | None = None,
    ) -> tuple[StorySessionState, NarrativeTurnResult]:
        """Run one turn inside an open freeform segment.

        The action is ruled first and narrated second. Both provider calls share
        the same request prefix (the narration call merely appends to it), so the
        provider cache keeps hitting across the pair and across turns.

        A rejected action writes prose and no state: the world answers in-story.
        """
        if request_id is not None:
            try:
                cached = self.store.load_story_turn_result(
                    request_id, state.session_id, state.branch_id
                )
            except ValueError as exc:
                raise RuleViolation(str(exc)) from exc
            if cached is not None:
                return self.store.load_story_snapshot(cached.session_id, cached.branch_id), cached

        text = action.strip()
        if not text:
            raise RuleViolation("a freeform action cannot be empty")

        persisted = self.store.load_story_snapshot(state.session_id, state.branch_id)
        if persisted != state:
            raise StoryConflict("conflicting story turn commit: stale state; reload before writing")
        if state.story_id != self.bundle.story_id or (
            state.bundle_digest is not None and state.bundle_digest != self.bundle.content_digest
        ):
            raise StoryConflict("story bundle changed; restore the session's original bundle")
        if state.status != "active":
            raise RuleViolation("story session is not active")

        segment = state.active_segment
        if segment is None or segment.status != "open":
            raise RuleViolation("no freeform segment is open; use story-read for beat transitions")

        current = self.bundle.beat(state.current_beat_id)
        turn = state.turn_number + 1
        prefix = build_static_prefix(self.bundle, state)
        events = self.store.story_events(state.session_id, state.branch_id)
        messages = build_messages(prefix, events)

        self._progress("player_action", turn=turn, segment_id=segment.segment_id)
        # Both calls below extend the same prefix, so the cache stays warm.
        ruling, ruling_debug = await self.author.rule_action(messages, text)
        # Validate the declared consequences before spending the narration call.
        if ruling.feasible:
            _apply_state_patches(state, ruling.consequences)

        self._progress("authoring", turn=turn)
        prose = await self.author.narrate_action(messages, text, ruling)
        if not prose.strip():
            raise RuleViolation("narrative author returned empty prose")

        resolved = bool(ruling.tension_resolved)
        record = FreeformAction(
            turn=turn,
            player_input=text,
            feasible=bool(ruling.feasible),
            reason=ruling.reason,
            world_response=prose,
            patches=list(ruling.consequences),
            tension_resolved=resolved,
        )

        base = _apply_state_patches(state, ruling.consequences) if ruling.feasible else state
        trial = base.model_dump(mode="python")
        trial["version"] = state.version + 1
        trial["turn_number"] = turn
        trial["last_narrative"] = prose
        grown = segment.model_copy(deep=True)
        grown.actions.append(record)
        if resolved:
            grown.status = "resolved"
            grown.resolution = ruling.resolution.strip() or text
            trial["active_segment"] = None
            trial["available_choices"] = [
                NarrativeChoice.from_spec(choice).model_dump() for choice in current.choices
            ]
        else:
            trial["active_segment"] = grown.model_dump(mode="python")
            trial["available_choices"] = []
        new_state = StorySessionState.model_validate(trial)

        tx = self.store.begin_story_turn(
            state.session_id, state.branch_id, turn, expected_version=state.version
        )
        try:
            tx.append(
                "story_player_input_received",
                {
                    "text": text,
                    "input_mode": "freeform",
                    "choice_id": None,
                    "automatic": False,
                    "segment_id": segment.segment_id,
                },
            )
            tx.append(
                "story_action_ruled",
                {
                    "segment_id": segment.segment_id,
                    "feasible": bool(ruling.feasible),
                    "reason": ruling.reason,
                    "consequences": [
                        patch.model_dump(mode="json") for patch in ruling.consequences
                    ],
                    "tension_resolved": resolved,
                    "debug": ruling_debug,
                },
            )
            tx.append(
                "story_narrative_emitted",
                {"beat_id": current.beat_id, "text": prose, "source_refs": []},
            )
            tx.append(
                "story_state_updated",
                {
                    "current_beat_id": new_state.current_beat_id,
                    "completed_beat_ids": new_state.completed_beat_ids,
                    "revealed_fact_ids": sorted(new_state.revealed_fact_ids),
                    "patches": [
                        patch.model_dump(mode="json")
                        for patch in (ruling.consequences if ruling.feasible else [])
                    ],
                    "version": new_state.version,
                    "decisions": [decision.model_dump() for decision in new_state.decisions],
                },
            )
            if resolved:
                tx.append(
                    "story_segment_resolved",
                    {"segment_id": segment.segment_id, "resolution": grown.resolution},
                )
            tx.append(
                "story_turn_completed",
                {"status": new_state.status, "version": new_state.version},
            )
            result = NarrativeTurnResult(
                session_id=new_state.session_id,
                story_id=new_state.story_id,
                branch_id=new_state.branch_id,
                turn_number=turn,
                player_input=text,
                input_mode="freeform",
                choice_id=None,
                narrative=prose,
                narrative_beat_id=current.beat_id,
                current_beat_id=new_state.current_beat_id,
                choices=new_state.available_choices,
                revealed_fact_ids=[],
                source_refs=[],
                ended=new_state.status == "completed",
                debug={
                    "feasible": bool(ruling.feasible),
                    "tension_resolved": resolved,
                    "segment_id": segment.segment_id,
                    **ruling_debug,
                },
            )
        except Exception as exc:
            tx.abort(str(exc))
            raise

        tx.commit(new_state, request_id=request_id, result=result)
        self._progress("completed", turn=turn, branch_id=new_state.branch_id)
        return new_state, result

    @staticmethod
    def _validate_legacy_transition(
        authored: NarrativeAuthorProposal, resolved: NarrativeAuthorProposal
    ) -> None:
        excluded = {"narrative", "debug", "state_patches"}
        if authored.model_dump(exclude=excluded) != resolved.model_dump(exclude=excluded):
            raise RuleViolation("author metadata must match the runtime-resolved transition")

        def effects(proposal: NarrativeAuthorProposal) -> Counter[str]:
            return Counter(
                json.dumps(
                    {"operation": patch.operation, "path": patch.path, "value": patch.new_value},
                    sort_keys=True,
                )
                for patch in proposal.state_patches
            )

        if effects(authored) != effects(resolved):
            raise RuleViolation("author effects must match the runtime-resolved transition exactly")

    def _resolve_choice(
        self, state: StorySessionState, current: StoryBeat, incoming: NarrativeInput
    ) -> BeatChoice | None:
        if incoming.choice_id is None:
            return None
        if incoming.choice_id not in {choice.choice_id for choice in state.available_choices}:
            raise RuleViolation(f"choice is not available: {incoming.choice_id}")
        for choice in current.choices:
            if choice.choice_id == incoming.choice_id:
                return choice
        raise RuleViolation(f"choice is not valid for current beat: {incoming.choice_id}")

    def _validate_proposal(
        self,
        state: StorySessionState,
        current: StoryBeat,
        selected_choice: BeatChoice | None,
        proposal: NarrativeAuthorProposal,
    ) -> None:
        if not proposal.narrative.strip():
            raise RuleViolation("narrative author returned empty prose")
        expected_beat_id = selected_choice.next_beat_id if selected_choice else current.beat_id
        if proposal.narrative_beat_id != expected_beat_id:
            raise RuleViolation(
                f"author wrote beat {proposal.narrative_beat_id!r}; expected {expected_beat_id!r}"
            )
        if proposal.next_beat_id != expected_beat_id:
            raise RuleViolation("author may only advance to the resolved story beat")
        target = self.bundle.beat(expected_beat_id)
        if proposal.ended != target.terminal:
            raise RuleViolation("proposal terminal status does not match the story bundle")
        if not selected_choice and proposal.advance_beat:
            raise RuleViolation("freeform input cannot advance a beat without a choice")
        if selected_choice and not proposal.advance_beat:
            raise RuleViolation("a selected choice must advance the story beat")
        choice_ids = [choice.choice_id for choice in proposal.choices]
        if len(choice_ids) != len(set(choice_ids)):
            raise RuleViolation("author returned duplicate choice ids")
        for choice in proposal.choices:
            self.bundle.beat(choice.next_beat_id)
        expected_choices = [NarrativeChoice.from_spec(choice) for choice in target.choices]
        if proposal.choices != expected_choices:
            raise RuleViolation("author choices must match the current story beat")
        if len(proposal.revealed_fact_ids) != len(set(proposal.revealed_fact_ids)):
            raise RuleViolation("author returned duplicate revealed facts")
        known_fact_ids = {fact.fact_id for fact in self.bundle.canon_facts}
        if set(proposal.revealed_fact_ids) - known_fact_ids:
            raise RuleViolation("proposal contains an unknown revealed fact")
        allowed_reveals = set(selected_choice.reveal_fact_ids) if selected_choice else set()
        if set(proposal.revealed_fact_ids) - allowed_reveals:
            raise RuleViolation("author revealed a fact not allowed by the selected choice")
        for fact_id in proposal.revealed_fact_ids:
            fact = self.bundle.fact(fact_id)
            if fact.visibility == FactVisibility.AUTHOR_ONLY:
                raise RuleViolation(f"author-only fact cannot be revealed: {fact_id}")

        _apply_state_patches(state, proposal.state_patches)
        declared_effects = [
            {
                "operation": effect.operation,
                "path": effect.path,
                "new_value": effect.value,
            }
            for effect in (selected_choice.effects if selected_choice else [])
        ]
        for patch in proposal.state_patches:
            if patch.path in {"variables.last_input", "variables.last_choice"}:
                if patch.operation != "set":
                    raise RuleViolation(f"invalid bookkeeping patch operation: {patch.path}")
                continue
            candidate = {
                "operation": patch.operation,
                "path": patch.path,
                "new_value": patch.new_value,
            }
            if candidate not in declared_effects:
                raise RuleViolation(f"author proposed an undeclared state effect: {patch.path}")

    def _commit_proposal(
        self,
        state: StorySessionState,
        current: StoryBeat,
        proposal: NarrativeAuthorProposal,
        turn: int,
        selected_choice: BeatChoice | None,
    ) -> StorySessionState:
        completed = list(state.completed_beat_ids)
        if proposal.advance_beat and current.beat_id not in completed:
            completed.append(current.beat_id)
        revealed = set(state.revealed_fact_ids)
        revealed.update(proposal.revealed_fact_ids)
        decisions = list(state.decisions)
        if selected_choice is not None and current.decision_required:
            decisions.append(
                StoryDecision(
                    turn_number=turn,
                    beat_id=current.beat_id,
                    choice_id=selected_choice.choice_id,
                    text=selected_choice.text,
                    consequence_hint=selected_choice.narrative_hint,
                )
            )
        return state.model_copy(
            update={
                "turn_number": turn,
                "current_beat_id": proposal.next_beat_id,
                "completed_beat_ids": completed,
                "available_choices": proposal.choices,
                "revealed_fact_ids": revealed,
                "last_narrative": proposal.narrative,
                "decisions": decisions,
                "status": "completed" if proposal.ended else "active",
            }
        )
