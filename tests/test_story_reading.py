"""Bounded reading, persistent request recovery, and consequential branching."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from trpg_runtime.cli import app
from trpg_runtime.narrative import (
    FakeNarrativeAuthor,
    LLMSettings,
    NarrativeInput,
    NarrativeOrchestrator,
    OpenAINarrativeAuthor,
    PlayerIdentity,
    ReadingRequest,
    StoryReader,
    StoryStore,
)
from trpg_runtime.narrative.storage import StoryConflict
from trpg_runtime.narrative.workflow import import_bundle
from trpg_runtime.resource_library import ResourceLibrary
from trpg_runtime.story import load_bundle
from trpg_runtime.story.bundle import StoryBundle
from trpg_runtime.web.app import create_app

SAMPLE = Path("examples/story/last_ferry.yaml")


class CountingAuthor(FakeNarrativeAuthor):
    def __init__(self, fail_at=None, cancelled=False):
        self.calls = 0
        self.fail_at = fail_at
        self.cancelled = cancelled

    async def generate(self, *args, **kwargs):
        self.calls += 1
        if self.calls == self.fail_at:
            if self.cancelled:
                raise asyncio.CancelledError()
            raise RuntimeError("writer interrupted")
        return await super().generate(*args, **kwargs)


def setup_reader(tmp_path, author=None):
    bundle = load_bundle(SAMPLE)
    store = StoryStore(tmp_path / "reading.db")
    runtime = NarrativeOrchestrator(store, bundle, author or CountingAuthor())
    asyncio.run(runtime.start_session(PlayerIdentity(display_name="林岑"), session_id="ferry"))
    return bundle, store, runtime, StoryReader(runtime)


def read(reader, key, scenes=8, choice=None, branch="main"):
    return asyncio.run(
        reader.read(
            "ferry",
            ReadingRequest(request_id=key, max_scenes=scenes, choice_id=choice),
            branch,
        )
    )


def test_reading_stops_at_decision_and_replays_without_calls(tmp_path):
    author = CountingAuthor()
    _, store, _, reader = setup_reader(tmp_path, author)
    result = read(reader, "opening")
    assert result.stop_reason == "awaiting_choice"
    assert [scene.narrative_beat_id for scene in result.scenes] == ["rain", "decision"]
    assert {choice.choice_id for choice in result.choices} == {"stay", "cross"}
    assert store.load_story_snapshot("ferry").decisions == []
    events = store.story_events("ferry")
    assert read(reader, "opening") == result
    assert author.calls == 2
    assert store.story_events("ferry") == events
    assert read(reader, "wait-again").scenes == []
    assert author.calls == 2


def test_budget_is_for_the_whole_request_not_each_retry(tmp_path):
    author = CountingAuthor()
    _, _, _, reader = setup_reader(tmp_path, author)
    first = read(reader, "one", scenes=1)
    assert first.stop_reason == "budget_exhausted"
    assert first.current_beat_id == "rain"
    assert read(reader, "one", scenes=1) == first
    assert author.calls == 1
    second = read(reader, "two", scenes=1)
    assert second.stop_reason == "awaiting_choice"
    assert second.current_beat_id == "decision"
    # Replay remains stable even after the story has advanced.
    assert read(reader, "one", scenes=1) == first
    assert author.calls == 2


@pytest.mark.parametrize("cancelled", [False, True])
def test_restart_reuses_published_scenes_after_failure_or_cancellation(tmp_path, cancelled):
    author = CountingAuthor(fail_at=2, cancelled=cancelled)
    bundle, store, _, reader = setup_reader(tmp_path, author)
    with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
        read(reader, "resume")
    assert store.load_story_snapshot("ferry").turn_number == 1
    reopened = StoryStore(tmp_path / "reading.db")
    replacement = CountingAuthor()
    resumed = StoryReader(NarrativeOrchestrator(reopened, bundle, replacement))
    result = read(resumed, "resume")
    assert result.stop_reason == "awaiting_choice"
    assert len(result.scenes) == 2
    assert replacement.calls == 1
    assert sum(e["type"] == "story_narrative_emitted" for e in reopened.story_events("ferry")) == 2


def test_crash_after_last_scene_before_report_does_not_generate_again(tmp_path, monkeypatch):
    bundle, store, _, reader = setup_reader(tmp_path)

    def interrupted(*args):
        raise RuntimeError("report write interrupted")

    monkeypatch.setattr(store, "finish_reading_run", interrupted)
    with pytest.raises(RuntimeError, match="report write"):
        read(reader, "report")
    author = CountingAuthor()
    reopened = StoryReader(
        NarrativeOrchestrator(StoryStore(tmp_path / "reading.db"), bundle, author)
    )
    result = read(reopened, "report")
    assert len(result.scenes) == 2
    assert result.stop_reason == "awaiting_choice"
    assert author.calls == 0


def test_unrelated_turn_during_interruption_cannot_be_silently_consumed(tmp_path):
    bundle, store, _, reader = setup_reader(tmp_path, CountingAuthor(fail_at=2))
    with pytest.raises(RuntimeError):
        read(reader, "interrupted")
    runtime = NarrativeOrchestrator(store, bundle, CountingAuthor())
    asyncio.run(
        runtime.process_turn(
            store.load_story_snapshot("ferry"),
            NarrativeInput(text="I look back."),
            request_id="manual",
        )
    )
    with pytest.raises(StoryConflict, match="outside this reading request"):
        read(StoryReader(runtime), "interrupted")


@pytest.mark.parametrize("change", ["budget", "choice", "branch", "session"])
def test_reading_request_identity_cannot_be_reused_for_other_work(tmp_path, change):
    _, _, runtime, reader = setup_reader(tmp_path)
    read(reader, "same")
    request = ReadingRequest(
        request_id="same",
        max_scenes=7 if change == "budget" else 8,
        choice_id="stay" if change == "choice" else None,
    )
    with pytest.raises(StoryConflict, match="different request"):
        asyncio.run(
            reader.read(
                "other" if change == "session" else "ferry",
                request,
                "other" if change == "branch" else "main",
            )
        )
    assert runtime.author.calls == 2


def test_one_option_still_requires_direction_when_explicitly_marked(tmp_path):
    bundle = load_bundle(SAMPLE)
    bundle.first_beat.decision_required = True
    runtime = NarrativeOrchestrator(StoryStore(tmp_path / "one.db"), bundle, CountingAuthor())
    asyncio.run(runtime.start_session(PlayerIdentity(display_name="林岑"), session_id="ferry"))
    result = read(StoryReader(runtime), "wait")
    assert result.stop_reason == "awaiting_choice"
    assert len(result.choices) == 1
    assert runtime.author.calls == 0


def test_automatic_scene_with_multiple_routes_is_rejected():
    document = load_bundle(SAMPLE).model_dump()
    document["story_beats"][2]["decision_required"] = False
    with pytest.raises(ValidationError, match="exactly one continuation"):
        StoryBundle.model_validate(document)


@pytest.mark.parametrize("budget", [0, 9])
def test_reading_budget_is_bounded(budget):
    with pytest.raises(ValidationError):
        ReadingRequest(request_id="invalid", max_scenes=budget)


def test_changed_bundle_is_rejected_before_writing(tmp_path):
    bundle, store, _, reader = setup_reader(tmp_path)
    read(reader, "initial", scenes=1)
    original = bundle.content_digest
    bundle.beat("rain").narrative = "A different story."
    assert bundle.content_digest != original
    author = CountingAuthor()
    runtime = NarrativeOrchestrator(store, bundle, author)
    with pytest.raises(StoryConflict, match="pinned bundle"):
        read(StoryReader(runtime), "changed")
    with pytest.raises(StoryConflict, match="bundle changed"):
        asyncio.run(runtime.process_turn(store.load_story_snapshot("ferry"), "Look around."))
    assert author.calls == 0
    assert store.load_story_snapshot("ferry").turn_number == 1


def test_legacy_save_requires_explicit_new_session_for_reading(tmp_path):
    _, store, _, reader = setup_reader(tmp_path)
    state = store.load_story_snapshot("ferry")
    state.bundle_digest = None
    store.save_story_snapshot(state)
    with pytest.raises(StoryConflict, match="legacy save"):
        read(reader, "legacy")


def test_one_intervention_changes_later_scenes_and_persisted_consequences(tmp_path):
    bundle, store, runtime, reader = setup_reader(tmp_path)
    read(reader, "opening")
    at_fork = store.load_story_snapshot("ferry")
    runtime.fork(at_fork, "crossing")
    stayed = read(reader, "stay", choice="stay")
    crossed = read(reader, "cross", choice="cross", branch="crossing")
    assert stayed.stop_reason == crossed.stop_reason == "completed"
    assert [s.narrative_beat_id for s in stayed.scenes] == ["shelter", "night", "shelter-end"]
    assert [s.narrative_beat_id for s in crossed.scenes] == ["boat", "crossing", "delivered"]
    assert "没有送达" in stayed.scenes[-1].narrative
    assert "送信已完成" in crossed.scenes[-1].narrative
    main = store.load_story_snapshot("ferry")
    child = store.load_story_snapshot("ferry", "crossing")
    assert main.variables["letter_delivered"] is False
    assert child.variables["letter_delivered"] is True
    assert [item.choice_id for item in main.decisions] == ["stay"]
    assert [item.choice_id for item in child.decisions] == ["cross"]
    assert main.bundle_digest == child.bundle_digest == bundle.content_digest


def test_imported_source_can_be_read_without_choosing_continue_each_time(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("# Story\n\n## One\nRain.\n\n## Two\nA boat.\n\n## Three\nHome.")
    _, _, bundle = import_bundle(str(source), output=str(tmp_path / "bundle.yaml"))
    runtime = NarrativeOrchestrator(StoryStore(tmp_path / "source.db"), bundle, CountingAuthor())
    asyncio.run(runtime.start_session(PlayerIdentity(display_name="Reader"), session_id="ferry"))
    result = read(StoryReader(runtime), "source")
    assert result.stop_reason == "completed"
    assert len(result.scenes) == 2


def test_real_provider_adapter_keeps_decision_in_later_scene_prompts(tmp_path):
    bundle, store, runtime, reader = setup_reader(tmp_path)
    read(reader, "opening")
    prompts = []
    reviews = []

    def handler(request):
        prompt = json.loads(json.loads(request.content)["messages"][1]["content"])
        if "draft" in prompt:
            reviews.append(prompt)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"accepted":true,"violations":[]}',
                            }
                        }
                    ]
                },
            )
        prompts.append(prompt)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "narrative": "Scene " + prompt["target_scene"]["id"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    async def run():
        author = OpenAINarrativeAuthor(
            LLMSettings("mock", "https://example.invalid/v1", "unused", "test"),
            transport=httpx.MockTransport(handler),
        )
        try:
            return await StoryReader(NarrativeOrchestrator(store, bundle, author)).read(
                "ferry",
                ReadingRequest(request_id="written", max_scenes=8, choice_id="stay"),
            )
        finally:
            await author.aclose()

    result = asyncio.run(run())
    assert result.stop_reason == "completed"
    assert len(prompts) == 3
    assert len(reviews) == 3
    assert prompts[0]["resolved_choice"]["kind"] == "decision"
    assert prompts[-1]["resolved_choice"]["kind"] == "continuation"
    assert prompts[-1]["player_decisions"][0]["choice_id"] == "stay"
    assert prompts[-1]["committed_state"]["variables"]["letter_delivered"] is False
    assert "继续阅读" not in [e["text"] for e in prompts[-1]["recent_public_history"]]


def test_cli_reading_and_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_DB_PATH", str(tmp_path / "cli.db"))
    runner = CliRunner()
    created = runner.invoke(app, ["story-new", str(SAMPLE), "--session-id", "ferry"])
    assert created.exit_code == 0, created.output
    result = runner.invoke(app, ["story-read", str(SAMPLE), "ferry", "--request-id", "cli-start"])
    assert result.exit_code == 0, result.output
    assert "Your direction is needed" in result.output
    chosen = runner.invoke(
        app,
        [
            "story-read",
            str(SAMPLE),
            "ferry",
            "--choice",
            "stay",
            "--request-id",
            "cli-stay",
        ],
    )
    assert chosen.exit_code == 0, chosen.output
    assert "Story completed" in chosen.output
    assert (
        StoryStore(tmp_path / "cli.db").load_story_snapshot("ferry").decisions[0].choice_id
        == "stay"
    )


def test_http_reading_replay_validation_and_choice(tmp_path):
    shutil.copy(SAMPLE, tmp_path / "story.yaml")
    shutil.copy("config/agents.yaml", tmp_path / "agents.yaml")
    web = create_app(
        db_path=str(tmp_path / "web.db"),
        config_path=str(tmp_path / "agents.yaml"),
        library=ResourceLibrary([tmp_path]),
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=web), base_url="http://test"
        ) as client:
            res = await client.post(
                "/api/story/sessions",
                json={
                    "story_id": "last-ferry",
                    "session_id": "ferry",
                    "player_name": "林岑",
                },
            )
            assert res.status_code == 200, res.text
            endpoint = "/api/story/sessions/ferry/read"
            first = await client.post(endpoint, json={"request_id": "first", "max_scenes": 8})
            assert first.status_code == 200, first.text
            assert first.json()["stop_reason"] == "awaiting_choice"
            assert all("debug" not in scene for scene in first.json()["scenes"])
            replay = await client.post(endpoint, json={"request_id": "first", "max_scenes": 8})
            assert replay.json() == first.json()
            changed = await client.post(endpoint, json={"request_id": "first", "max_scenes": 1})
            assert changed.status_code == 409
            invalid = await client.post(endpoint, json={"request_id": "invalid", "max_scenes": 99})
            assert invalid.status_code == 422
            selected = await client.post(endpoint, json={"request_id": "stay", "choice_id": "stay"})
            assert selected.status_code == 200, selected.text
            assert selected.json()["stop_reason"] == "completed"

    asyncio.run(run())
