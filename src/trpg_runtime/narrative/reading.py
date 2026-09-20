"""Bounded, resumable reading over explicitly automatic Story scenes."""

from typing import Literal

from .domain import NarrativeInput, ReadingBatch, ReadingRequest, ReadingScene
from .runtime import NarrativeOrchestrator
from .storage import StoryConflict


class StoryReader:
    def __init__(self, runtime: NarrativeOrchestrator):
        self.runtime = runtime

    async def read(
        self, session_id: str, request: ReadingRequest, branch_id: str = "main"
    ) -> ReadingBatch:
        runtime = self.runtime
        run = runtime.store.open_reading_run(
            session_id, branch_id, request, runtime.bundle.content_digest
        )
        if run.result is not None:
            return run.result
        scenes: list[ReadingScene] = []
        # Each scene's deterministic internal request key is committed atomically
        # with its snapshot. Recovery requires no fragile second progress write.
        for index in range(request.max_scenes + 1):
            key = f"reading:{run.run_id}:{index}"
            cached = runtime.store.load_story_turn_result(key, session_id, branch_id)
            if cached is not None:
                scenes.append(ReadingScene.model_validate(cached.model_dump()))
                continue
            state = runtime.store.load_story_snapshot(session_id, branch_id)
            if state.turn_number != run.start_turn + len(
                scenes
            ) or state.version != run.start_version + len(scenes):
                raise StoryConflict(
                    "story advanced outside this reading request; reload to continue"
                )
            current = runtime.bundle.beat(state.current_beat_id)
            choice_id = request.choice_id if index == 0 else None
            reason: Literal["completed", "awaiting_choice", "budget_exhausted"]
            if state.status == "completed":
                if choice_id is not None:
                    raise ValueError("cannot choose in a completed story")
                reason = "completed"
            elif state.status != "active":
                raise ValueError("story is not active")
            elif current.decision_required and choice_id is None:
                reason = "awaiting_choice"
            elif index == request.max_scenes:
                reason = "budget_exhausted"
            else:
                if choice_id is None:
                    if len(current.choices) != 1:
                        raise ValueError("automatic scene must have exactly one continuation")
                    choice_id = current.choices[0].choice_id
                _, result = await runtime.process_turn(
                    state, NarrativeInput(choice_id=choice_id, input_mode="choice"), request_id=key
                )
                scenes.append(ReadingScene.model_validate(result.model_dump()))
                continue
            batch = ReadingBatch(
                request_id=request.request_id,
                session_id=session_id,
                branch_id=branch_id,
                stop_reason=reason,
                scenes=scenes,
                current_beat_id=state.current_beat_id,
                turn_number=state.turn_number,
                version=state.version,
                choices=state.available_choices if reason == "awaiting_choice" else [],
            )
            return runtime.store.finish_reading_run(run, batch)
        raise AssertionError("reading budget exhausted without a stop result")
