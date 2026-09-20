from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agents import AgentSuite, FakeAgentSuite, PydanticAISuite
from ..composer import ComposeRequest, build_preview, compose_state, create_campaign
from ..config import RuntimeConfig, load_runtime_config
from ..gm_docs import RULES_PRESETS
from ..llm import resolve_llm_settings
from ..narrative import (
    CanonPolicy,
    FakeNarrativeAuthor,
    NarrativeInput,
    NarrativeOrchestrator,
    OpenAINarrativeAuthor,
    PlayerIdentity,
    ReadingRequest,
    StoryReader,
    StoryStore,
)
from ..narrative.providers import SceneReviewRejected
from ..narrative.storage import StoryConflict
from ..resource_library import ResourceLibrary
from ..runtime import TurnOrchestrator
from ..storage import EventStore

STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_KINDS = {"scenarios", "cards", "worlds"}
logger = logging.getLogger("tari.web")


def _story_store(db_path: str) -> StoryStore:
    return StoryStore(db_path)


def _story_author_for(fake: bool):
    if fake:
        return FakeNarrativeAuthor()
    return OpenAINarrativeAuthor(resolve_llm_settings())


class TurnRequest(BaseModel):
    player_input: str
    request_id: str | None = None
    fake: bool = False
    stream: bool = True


class StorySessionRequest(BaseModel):
    story_id: str
    session_id: str | None = None
    player_name: str = "Player"
    identity_type: str = "visitor"
    persona: str = ""
    host_character: str | None = None
    canon_policy: str = "guided"
    seed: int = 0


class StoryTurnRequest(BaseModel):
    text: str = ""
    choice_id: str | None = None
    input_mode: str = "freeform"
    request_id: str | None = None
    fake: bool = True


class StoryReadRequest(ReadingRequest):
    fake: bool = True


def _state_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="campaign not found")


def create_app(
    *,
    db_path: str | None = None,
    config_path: str | None = None,
    library: ResourceLibrary | None = None,
    agent_factory: Callable[[bool, str], AgentSuite] | None = None,
) -> FastAPI:
    app = FastAPI(title="TARI Web Console")
    resolved_db = db_path or os.getenv("TRPG_DB_PATH") or "runtime-data/trpg.db"
    resolved_config = config_path or os.getenv("TARI_CONFIG_PATH") or "config/agents.yaml"
    app.state.store = EventStore(resolved_db)
    app.state.config_path = Path(resolved_config)
    app.state.library = library or ResourceLibrary()
    app.state.library.scan()

    def default_agent_factory(fake: bool, locale: str) -> AgentSuite:
        if fake:
            return FakeAgentSuite(locale)
        return PydanticAISuite(load_runtime_config(app.state.config_path), locale)

    app.state.agent_factory = agent_factory or default_agent_factory

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled error on %s %s:\n%s",
            request.method,
            request.url.path,
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": f"{type(exc).__name__}: {exc}"},
        )

    @app.get("/api/resources")
    def list_resources(request: Request) -> dict[str, Any]:
        lib: ResourceLibrary = request.app.state.library
        return {
            "scenarios": [r.to_dict() for r in lib.by_kind("scenarios")],
            "cards": [r.to_dict() for r in lib.by_kind("cards")],
            "worlds": [r.to_dict() for r in lib.by_kind("worlds")],
            "stories": [r.to_dict() for r in lib.by_kind("stories")],
            "rulesets": [{"id": key, "text": text} for key, text in RULES_PRESETS.items()],
            "warnings": lib.warnings,
        }

    @app.get("/api/resources/avatar")
    def card_avatar(resource_id: str, request: Request) -> FileResponse:
        lib: ResourceLibrary = request.app.state.library
        res = lib.get("cards", resource_id)
        if res is None or not res.meta.get("avatar"):
            raise HTTPException(status_code=404, detail="no avatar for this card")
        return FileResponse(res.path)

    @app.post("/api/resources/preview")
    async def preview(body: dict[str, Any], request: Request) -> dict[str, Any]:
        lib: ResourceLibrary = request.app.state.library
        try:
            return build_preview(lib, ComposeRequest(**body))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/resources/{kind}")
    async def upload_resource(kind: str, file: UploadFile, request: Request) -> dict[str, Any]:
        if kind not in UPLOAD_KINDS:
            raise HTTPException(status_code=400, detail=f"unknown resource kind: {kind}")
        data = await file.read()
        lib: ResourceLibrary = request.app.state.library
        try:
            res = lib.save_upload(kind, file.filename or "upload", data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return res.to_dict()

    @app.get("/api/campaigns")
    def list_campaigns(request: Request) -> dict[str, Any]:
        store: EventStore = request.app.state.store
        return {"campaigns": store.list_campaigns()}

    @app.post("/api/campaigns")
    async def create_campaign_route(
        body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        store: EventStore = request.app.state.store
        lib: ResourceLibrary = request.app.state.library
        try:
            state = compose_state(lib, ComposeRequest(**body))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if store.has_campaign(state.campaign_id):
            raise HTTPException(
                status_code=409, detail=f"campaign already exists: {state.campaign_id}"
            )
        create_campaign(store, state)
        return {
            "campaign_id": state.campaign_id,
            "title": state.title,
            "locale": state.locale,
            "turn_number": 0,
            "status": state.status,
        }

    @app.get("/api/campaigns/{campaign_id}/state")
    def campaign_state(campaign_id: str, request: Request, view: str = "public") -> dict[str, Any]:
        store: EventStore = request.app.state.store
        state = store.load_snapshot(campaign_id)
        if state is None:
            raise _state_not_found()
        actor = next(iter(state.actors.values()))
        transcript: list[dict[str, Any]] = []
        for event in store.events(campaign_id):
            payload = event["payload"]
            if event["type"] == "player_action_received":
                transcript.append(
                    {"type": "player", "turn": event["turn"], "text": payload.get("text", "")}
                )
            elif event["type"] == "dice_rolled":
                transcript.append(
                    {
                        "type": "dice",
                        "turn": event["turn"],
                        "rolls": list(payload.get("rolls") or ()),
                        "total": payload.get("total"),
                        "outcome": payload.get("outcome"),
                    }
                )
            elif event["type"] == "public_narrative_emitted":
                speaker = payload.get("speaker")
                if speaker == "gm":
                    transcript.append(
                        {"type": "gm", "turn": event["turn"], "text": payload.get("text", "")}
                    )
                elif speaker not in (None, "player"):
                    transcript.append(
                        {"type": "actor", "turn": event["turn"], "text": payload.get("text", "")}
                    )
        data: dict[str, Any] = {
            "campaign_id": state.campaign_id,
            "title": state.title,
            "opening": state.opening,
            "turn_number": state.turn_number,
            "locale": state.locale,
            "status": state.status,
            "transcript": transcript,
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
            },
            "spotlight": state.spotlight.model_dump(mode="json"),
            "rules": {
                "ruleset_id": state.rules.ruleset_id,
                "custom_rules": state.rules.custom_rules,
            },
        }
        if view == "gm":
            data["scene"]["hidden_facts"] = state.scene.hidden_facts
            data["actor"]["knowledge"] = [k.model_dump(mode="json") for k in actor.knowledge]
            data["actor"]["secrets"] = actor.secrets
            data["story_framework"] = state.story_framework.model_dump(mode="json")
            data["events"] = store.events(campaign_id)
        return data

    @app.post("/api/campaigns/{campaign_id}/turns")
    async def play_turn(
        campaign_id: str, body: TurnRequest, request: Request
    ) -> Response:
        store: EventStore = request.app.state.store
        state = store.load_snapshot(campaign_id)
        if state is None:
            raise _state_not_found()

        cached = body.request_id is not None and store.load_turn_result(body.request_id) is not None

        if not body.stream:
            agents = request.app.state.agent_factory(body.fake, state.locale)
            runtime = TurnOrchestrator(store, agents)
            new_state, result = await runtime.process_turn(
                state, body.player_input, request_id=body.request_id
            )
            actor_text = "\n".join(
                x for x in [result.actor_action, result.actor_speech] if x
            )
            return JSONResponse(
                {
                    "turn": result.turn_number,
                    "version": new_state.version,
                    "cached": cached,
                    "roll": (
                        {
                            "rolls": list(result.roll.rolls),
                            "total": result.roll.total,
                            "outcome": result.roll.outcome.value,
                        }
                        if result.roll is not None
                        else None
                    ),
                    "gm_narration": result.gm_narration,
                    "gm_wrap_narration": result.gm_wrap_narration,
                    "actor_text": actor_text or None,
                }
            )

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def emit(event: str, data: dict[str, Any]) -> None:
            queue.put_nowait({"event": event, "data": data})

        async def run_turn() -> None:
            try:
                agents = request.app.state.agent_factory(body.fake, state.locale)

                def on_progress(stage: str, payload: dict[str, Any]) -> None:
                    if stage == "dice":
                        emit("dice", payload)
                    else:
                        emit("stage", {"stage": stage, **payload})

                runtime = TurnOrchestrator(store, agents, on_progress=on_progress)
                new_state, result = await runtime.process_turn(
                    state, body.player_input, request_id=body.request_id
                )
                if result.roll is not None:
                    emit(
                        "dice",
                        {
                            "rolls": list(result.roll.rolls),
                            "total": result.roll.total,
                            "outcome": result.roll.outcome.value,
                        },
                    )
                if result.gm_narration:
                    emit(
                        "message",
                        {
                            "speaker": "gm",
                            "text": result.gm_narration,
                            "turn": result.turn_number,
                        },
                    )
                actor_text = "\n".join(
                    x for x in [result.actor_action, result.actor_speech] if x
                )
                if actor_text:
                    emit(
                        "message",
                        {"speaker": "actor", "text": actor_text, "turn": result.turn_number},
                    )
                if result.gm_wrap_narration:
                    emit(
                        "message",
                        {
                            "speaker": "gm",
                            "text": result.gm_wrap_narration,
                            "turn": result.turn_number,
                        },
                    )
                emit(
                    "turn_complete",
                    {
                        "turn": result.turn_number,
                        "version": new_state.version,
                        "cached": cached,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - stream errors to the client
                emit("error", {"message": str(exc)})

        task = asyncio.create_task(run_turn())

        async def sse_gen():
            try:
                while True:
                    item = await queue.get()
                    yield (
                        f"event: {item['event']}\n"
                        f"data: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
                    )
                    if item["event"] in ("turn_complete", "error"):
                        break
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            sse_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/settings")
    def get_settings(request: Request) -> dict[str, Any]:
        config_path = Path(request.app.state.config_path)
        config = load_runtime_config(config_path)
        store: EventStore = request.app.state.store
        return {
            "config": config.model_dump(mode="json"),
            "key_status": {
                "provider": "deepseek",
                "configured": bool(os.getenv("DEEPSEEK_API_KEY")),
            },
            "config_path": str(config_path),
            "db_path": str(store.path),
        }

    @app.put("/api/settings")
    async def put_settings(body: dict[str, Any], request: Request) -> dict[str, Any]:
        try:
            config = RuntimeConfig.model_validate(body)
        except Exception as exc:  # noqa: BLE001 - surface validation errors
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        config_path = Path(request.app.state.config_path)
        tmp_path = config_path.with_suffix(".tmp")
        tmp_path.write_text(
            yaml.safe_dump(
                config.model_dump(mode="json"), sort_keys=False, allow_unicode=True
            ),
            encoding="utf-8",
        )
        tmp_path.replace(config_path)
        return {"config": config.model_dump(mode="json")}

    # --- Story Mode API --------------------------------------------------
    # The Story Mode runtime is deliberately separate from the traditional
    # TRPG campaign pipeline: it uses a different schema, different agent
    # contract, and a different SQLite table set. Endpoints below expose the
    # same create/state/turn/branch verbs without disturbing the legacy
    # ``/api/campaigns`` surface.

    def _resolve_story_resource(req: Request, story_id: str):
        lib: ResourceLibrary = req.app.state.library
        for res in lib.by_kind("stories"):
            if res.meta.get("story_id") == story_id:
                return res
        raise HTTPException(status_code=404, detail=f"unknown story: {story_id}")

    def _story_session_state_from_request(req: Request, session_id: str, branch_id: str = "main"):
        store = _story_store(req.app.state.store.path)
        try:
            return store.load_story_snapshot(session_id, branch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _story_bundle_for_state(req: Request, state):
        lib: ResourceLibrary = req.app.state.library
        resource = _resolve_story_resource(req, state.story_id)
        return lib.load_story_bundle(resource)

    def _serialize_story_state(state) -> dict[str, Any]:
        choices = [choice.model_dump(mode="json") for choice in state.available_choices]
        revealed = sorted(state.revealed_fact_ids)
        return {
            "session_id": state.session_id,
            "story_id": state.story_id,
            "bundle_digest": state.bundle_digest,
            "title": state.title,
            "branch_id": state.branch_id,
            "parent_branch_id": state.parent_branch_id,
            "turn_number": state.turn_number,
            "version": state.version,
            "locale": state.locale,
            "canon_policy": state.canon_policy.value,
            "current_beat_id": state.current_beat_id,
            "status": state.status,
            "player": state.player_identity.model_dump(mode="json"),
            "available_choices": choices,
            "completed_beat_ids": list(state.completed_beat_ids),
            "decisions": [decision.model_dump() for decision in state.decisions],
            "revealed_fact_ids": revealed,
            "variables": state.variables,
            "relationship_values": state.relationship_values,
            "last_narrative": state.last_narrative,
        }

    @app.post("/api/story/sessions")
    async def create_story_session(body: StorySessionRequest, request: Request) -> dict[str, Any]:
        res = _resolve_story_resource(request, body.story_id)
        lib: ResourceLibrary = request.app.state.library
        bundle = lib.load_story_bundle(res)
        try:
            identity = PlayerIdentity(
                display_name=body.player_name,
                identity_type=cast(
                    Literal["embody", "possess", "visitor", "replacement"],
                    body.identity_type,
                ),
                persona=body.persona,
                host_character=body.host_character,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid player identity: {exc}") from exc
        try:
            policy = CanonPolicy(body.canon_policy)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid canon policy: {exc}") from exc
        store = _story_store(request.app.state.store.path)
        if body.session_id and store.has_story_session(body.session_id):
            raise HTTPException(
                status_code=409, detail=f"story session already exists: {body.session_id}"
            )
        runtime = NarrativeOrchestrator(store, bundle, FakeNarrativeAuthor())
        state = await runtime.start_session(
            identity,
            session_id=body.session_id,
            seed=body.seed,
            canon_policy=policy,
        )
        return _serialize_story_state(state)

    @app.get("/api/story/sessions/{session_id}")
    async def story_session_state(
        session_id: str, request: Request, branch_id: str = "main"
    ) -> dict[str, Any]:
        state = _story_session_state_from_request(request, session_id, branch_id)
        return _serialize_story_state(state)

    @app.get("/api/story/sessions/{session_id}/branches")
    async def story_session_branches(session_id: str, request: Request) -> dict[str, Any]:
        store = _story_store(request.app.state.store.path)
        return {"branches": store.list_story_branches(session_id)}

    @app.get("/api/story/sessions/{session_id}/events")
    async def story_session_events(
        session_id: str, request: Request, branch_id: str = "main"
    ) -> dict[str, Any]:
        store = _story_store(request.app.state.store.path)
        return {"events": store.story_events(session_id, branch_id)}

    @app.post("/api/story/sessions/{session_id}/branches/{branch_id}")
    async def story_session_branch(
        session_id: str, branch_id: str, request: Request
    ) -> dict[str, Any]:
        store = _story_store(request.app.state.store.path)
        try:
            parent = store.load_story_snapshot(session_id, "main")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            child = store.create_story_branch(parent, branch_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _serialize_story_state(child)


    @app.post("/api/story/sessions/{session_id}/turns")
    async def story_session_turn(
        session_id: str,
        body: StoryTurnRequest,
        request: Request,
        branch_id: str = "main",
    ) -> dict[str, Any]:
        store = _story_store(request.app.state.store.path)
        try:
            state = store.load_story_snapshot(session_id, branch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        bundle = _story_bundle_for_state(request, state)

        try:
            incoming = NarrativeInput(
                text=body.text,
                choice_id=body.choice_id,
                input_mode=cast(
                    Literal["choice", "freeform", "continue"], body.input_mode
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        author = _story_author_for(body.fake)
        runtime = NarrativeOrchestrator(store, bundle, author)
        try:
            new_state, result = await runtime.process_turn(
                state, incoming, request_id=body.request_id
            )
        except StoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SceneReviewRejected as exc:
            raise HTTPException(status_code=502, detail={
                "code": "scene_review_rejected",
                "message": "Draft failed continuity review; published scenes are unchanged.",
            }) from exc
        except Exception as exc:  # noqa: BLE001 - surface validation errors
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await author.aclose()
        return {
            "state": _serialize_story_state(new_state),
            "result": {
                "turn": result.turn_number,
                "narrative": result.narrative,
                "narrative_beat_id": result.narrative_beat_id,
                "current_beat_id": result.current_beat_id,
                "choice_id": result.choice_id,
                "input_mode": result.input_mode,
                "choices": [choice.model_dump(mode="json") for choice in result.choices],
                "revealed_fact_ids": result.revealed_fact_ids,
                "source_refs": result.source_refs,
                "ended": result.ended,
            },
        }

    @app.post("/api/story/sessions/{session_id}/read")
    async def story_session_read(
        session_id: str, body: StoryReadRequest, request: Request, branch_id: str = "main"
    ) -> dict[str, Any]:
        state = _story_session_state_from_request(request, session_id, branch_id)
        bundle = _story_bundle_for_state(request, state)
        author = _story_author_for(body.fake)
        runtime = NarrativeOrchestrator(_story_store(request.app.state.store.path), bundle, author)
        try:
            result = await StoryReader(runtime).read(
                session_id,
                ReadingRequest(
                    request_id=body.request_id, max_scenes=body.max_scenes, choice_id=body.choice_id
                ),
                branch_id,
            )
        except StoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SceneReviewRejected as exc:
            raise HTTPException(status_code=502, detail={
                "code": "scene_review_rejected",
                "message": "Draft failed continuity review; resume with the same request_id.",
            }) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await author.aclose()
        return result.model_dump(mode="json")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
