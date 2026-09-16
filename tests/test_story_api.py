"""End-to-end tests for the Story Mode HTTP API.

These tests build a tiny but valid Story Bundle in a temporary directory,
expose it via the resource library, and then drive the new
``/api/story/...`` endpoints end-to-end. They intentionally avoid touching
the traditional ``/api/campaigns`` surface.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import httpx

from trpg_runtime.resource_library import ResourceLibrary
from trpg_runtime.story import write_bundle
from trpg_runtime.story.bundle import (
    BeatChoice,
    PlotArc,
    SourceManifest,
    StoryBeat,
    StoryBundle,
)
from trpg_runtime.web.app import create_app


def _make_bundle(tmp_path: Path) -> Path:
    stories_dir = tmp_path / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    bundle = StoryBundle(
        story_id="web-lantern",
        title="Web Lantern",
        locale="en",
        opening="The courier reaches the gate at dusk.",
        source=SourceManifest(kind="novel", label="fixture"),
        plot_arcs=[PlotArc(arc_id="arc-1", title="Arc", beat_ids=["arrival", "end"])],
        story_beats=[
            StoryBeat(
                beat_id="arrival",
                arc_id="arc-1",
                title="Arrival",
                dramatic_goal="Reach the gate",
                narrative="The courier reaches the gate at dusk.",
                choices=[
                    BeatChoice(choice_id="trust", text="Trust the keeper", next_beat_id="end")
                ],
            ),
            StoryBeat(
                beat_id="end",
                arc_id="arc-1",
                title="End",
                dramatic_goal="Close",
                narrative="The courier passes through.",
                terminal=True,
            ),
        ],
    )
    target = stories_dir / "web-lantern.yaml"
    write_bundle(target, bundle)
    return tmp_path


def _make_client(tmp_path: Path, library: ResourceLibrary | None = None):
    db = tmp_path / "story.db"
    config = tmp_path / "agents.yaml"
    shutil.copy("config/agents.yaml", config)
    app = create_app(
        db_path=str(db),
        config_path=str(config),
        library=library or ResourceLibrary([tmp_path]),
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


def test_story_resources_are_listed(tmp_path: Path):
    _make_bundle(tmp_path)

    async def run() -> None:
        async with _make_client(tmp_path) as client:
            res = await client.get("/api/resources")
            assert res.status_code == 200
            data = res.json()
            assert "stories" in data
            assert any(item.get("story_id") == "web-lantern" for item in data["stories"])

    asyncio.run(run())


def test_story_session_create_state_turn_branch(tmp_path: Path):
    _make_bundle(tmp_path)
    library = ResourceLibrary([tmp_path])

    async def run() -> None:
        async with _make_client(tmp_path, library=library) as client:
            create = await client.post(
                "/api/story/sessions",
                json={"story_id": "web-lantern", "session_id": "web-1", "player_name": "Ari"},
            )
            assert create.status_code == 200, create.text
            state = create.json()
            assert state["session_id"] == "web-1"
            assert state["current_beat_id"] == "arrival"
            assert {c["choice_id"] for c in state["available_choices"]} == {"trust"}

            get = await client.get("/api/story/sessions/web-1")
            assert get.status_code == 200
            assert get.json()["title"] == "Web Lantern"

            events = await client.get("/api/story/sessions/web-1/events")
            assert events.status_code == 200
            assert any(item["type"] == "story_session_created" for item in events.json()["events"])

            turn = await client.post(
                "/api/story/sessions/web-1/turns",
                json={"choice_id": "trust", "input_mode": "choice", "request_id": "r-1"},
            )
            assert turn.status_code == 200, turn.text
            payload = turn.json()
            assert payload["state"]["turn_number"] == 1
            assert payload["state"]["current_beat_id"] == "end"
            assert payload["result"]["ended"] is True

            # Idempotent request_id returns the cached turn result.
            cached = await client.post(
                "/api/story/sessions/web-1/turns",
                json={"choice_id": "trust", "input_mode": "choice", "request_id": "r-1"},
            )
            assert cached.status_code == 200
            assert cached.json()["result"]["turn"] == 1

            branch = await client.post(
                "/api/story/sessions/web-1/branches/hesitation",
            )
            assert branch.status_code == 200
            assert branch.json()["branch_id"] == "hesitation"

            branches = await client.get("/api/story/sessions/web-1/branches")
            assert {item["branch_id"] for item in branches.json()["branches"]} >= {
                "main",
                "hesitation",
            }

    asyncio.run(run())


def test_story_session_rejects_unknown_story(tmp_path: Path):
    _make_bundle(tmp_path)

    async def run() -> None:
        async with _make_client(tmp_path) as client:
            res = await client.post("/api/story/sessions", json={"story_id": "missing"})
            assert res.status_code == 404
            assert "unknown story" in res.json()["detail"]

    asyncio.run(run())


def test_invalid_choice_aborts_without_advancing(tmp_path: Path):
    _make_bundle(tmp_path)

    async def run() -> None:
        async with _make_client(tmp_path) as client:
            await client.post(
                "/api/story/sessions",
                json={"story_id": "web-lantern", "session_id": "web-2"},
            )
            bad = await client.post(
                "/api/story/sessions/web-2/turns",
                json={"choice_id": "invented", "input_mode": "choice"},
            )
            assert bad.status_code == 400
            again = await client.get("/api/story/sessions/web-2")
            assert again.json()["turn_number"] == 0
            assert again.json()["current_beat_id"] == "arrival"

    asyncio.run(run())


def test_llm_story_author_is_closed_after_a_turn(tmp_path: Path, monkeypatch) -> None:
    """A non-fake story turn must release its provider client, success or failure."""
    import trpg_runtime.web.app as web_app
    from trpg_runtime.narrative import FakeNarrativeAuthor

    _make_bundle(tmp_path)
    closed: list[bool] = []

    class ClosingAuthor(FakeNarrativeAuthor):
        async def aclose(self) -> None:
            closed.append(True)

    def factory(fake: bool):
        assert fake is False
        return ClosingAuthor()

    monkeypatch.setattr(web_app, "_story_author_for", factory)

    async def run() -> None:
        async with _make_client(tmp_path) as client:
            await client.post(
                "/api/story/sessions",
                json={"story_id": "web-lantern", "session_id": "web-3"},
            )
            turn = await client.post(
                "/api/story/sessions/web-3/turns",
                json={
                    "choice_id": "trust",
                    "input_mode": "choice",
                    "fake": False,
                    "request_id": "r-llm",
                },
            )
            assert turn.status_code == 200, turn.text
            bad = await client.post(
                "/api/story/sessions/web-3/turns",
                json={
                    "choice_id": "invented",
                    "input_mode": "choice",
                    "fake": False,
                    "request_id": "r-llm-bad",
                },
            )
            assert bad.status_code == 400

    asyncio.run(run())
    assert closed == [True, True]
