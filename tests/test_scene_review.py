"""Scene-boundary review must fail closed before publishing prose or state."""

import asyncio
import hashlib
import json
import shutil

import httpx
import pytest
from pydantic import ValidationError

from trpg_runtime.narrative import (
    LLMSettings,
    NarrativeOrchestrator,
    OpenAINarrativeAuthor,
    PlayerIdentity,
    ReadingRequest,
    StoryReader,
    StoryStore,
)
from trpg_runtime.narrative.providers import SceneReviewRejected
from trpg_runtime.resource_library import ResourceLibrary
from trpg_runtime.story import load_bundle
from trpg_runtime.story.bundle import SceneBoundary
from trpg_runtime.web.app import create_app


def author_with_review(review):
    captured = []

    def handler(request):
        payload = json.loads(request.content)
        captured.append(payload)
        content = json.loads(payload["messages"][1]["content"])
        reply = review if "draft" in content else {"narrative": "测试草稿：林岑越过了当前场景。"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(reply, ensure_ascii=False),
                        }
                    }
                ]
            },
        )

    return OpenAINarrativeAuthor(
        LLMSettings("mock", "https://example.invalid/v1", "unused", "test"),
        transport=httpx.MockTransport(handler),
    ), captured


@pytest.mark.parametrize(
    "review,error",
    [
        ({"accepted": False, "violations": ["玩家尚未决定，不能登船"]}, SceneReviewRejected),
        ({"accepted": True, "violations": ["前后位置冲突"]}, SceneReviewRejected),
        ({"accepted": False, "violations": []}, SceneReviewRejected),
        ({"accepted": "true", "violations": []}, ValidationError),
        ({"narrative": "This is not a review."}, ValidationError),
    ],
)
def test_rejected_or_invalid_review_leaves_no_published_scene(tmp_path, review, error):
    bundle = load_bundle("examples/story/last_ferry.yaml")
    store = StoryStore(tmp_path / "review.db")

    async def run():
        author, captured = author_with_review(review)
        runtime = NarrativeOrchestrator(store, bundle, author)
        initial = await runtime.start_session(
            PlayerIdentity(display_name="林岑"), session_id="test"
        )
        try:
            with pytest.raises(error):
                await StoryReader(runtime).read(
                    "test", ReadingRequest(request_id="review", max_scenes=1)
                )
        finally:
            await author.aclose()
        assert store.load_story_snapshot("test") == initial
        assert [event["type"] for event in store.story_events("test")] == [
            "story_session_created",
            "story_turn_aborted",
        ]
        assert len(captured) == 2
        writer_context = json.loads(captured[0]["messages"][1]["content"])
        reviewer_input = json.loads(captured[1]["messages"][1]["content"])
        assert reviewer_input["context"] == writer_context
        assert writer_context["target_scene"]["boundary"]["forbidden_events"]
        assert reviewer_input["draft"] == "测试草稿：林岑越过了当前场景。"
        with store.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM story_turn_results").fetchone()[0] == 0

    asyncio.run(run())


def test_accepted_review_is_diagnostic_and_not_reader_content(tmp_path):
    bundle = load_bundle("examples/story/last_ferry.yaml")
    store = StoryStore(tmp_path / "review.db")

    async def run():
        author, _ = author_with_review({"accepted": True, "violations": []})
        runtime = NarrativeOrchestrator(store, bundle, author)
        await runtime.start_session(PlayerIdentity(display_name="林岑"), session_id="test")
        try:
            result = await StoryReader(runtime).read(
                "test", ReadingRequest(request_id="ok", max_scenes=1)
            )
        finally:
            await author.aclose()
        assert len(result.scenes) == 1
        assert "scene_review" not in result.model_dump_json()
        with store.connect() as conn:
            saved = json.loads(
                conn.execute("SELECT result_json FROM story_turn_results").fetchone()[0]
            )
        assert saved["debug"]["scene_review"] == {"accepted": True, "violations": []}

    asyncio.run(run())


def test_optional_boundary_does_not_invalidate_existing_bundle_pins():
    bundle = load_bundle("examples/story/lantern_gate.yaml")
    old_document = bundle.model_dump(mode="json")
    for beat in old_document["story_beats"]:
        beat.pop("boundary")
    original = hashlib.sha256(
        json.dumps(old_document, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert bundle.content_digest == original
    bundle.first_beat.boundary = SceneBoundary(exit_facts=["The player has not entered the city."])
    assert bundle.content_digest != original


def test_scene_boundary_requires_an_explicit_stopping_state():
    with pytest.raises(ValidationError):
        SceneBoundary(entry_facts=["At the gate."], exit_facts=[])


def test_http_review_rejection_is_resumable_and_hides_review_details(tmp_path, monkeypatch):
    from trpg_runtime.web import app as web_app

    shutil.copy("examples/story/last_ferry.yaml", tmp_path / "story.yaml")
    shutil.copy("config/agents.yaml", tmp_path / "agents.yaml")
    web = create_app(
        db_path=str(tmp_path / "http.db"),
        config_path=str(tmp_path / "agents.yaml"),
        library=ResourceLibrary([tmp_path]),
    )
    monkeypatch.setattr(
        web_app,
        "_story_author_for",
        lambda fake: author_with_review(
            {
                "accepted": False,
                "violations": ["PRIVATE_REVIEW_DETAIL"],
            }
        )[0],
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=web), base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/story/sessions",
                json={
                    "story_id": "last-ferry",
                    "session_id": "review",
                    "player_name": "林岑",
                },
            )
            assert created.status_code == 200
            failed = await client.post(
                "/api/story/sessions/review/read",
                json={
                    "request_id": "review",
                    "max_scenes": 1,
                    "fake": False,
                },
            )
            assert failed.status_code == 502
            assert failed.json()["detail"]["code"] == "scene_review_rejected"
            assert "PRIVATE_REVIEW_DETAIL" not in failed.text
            snapshot = (await client.get("/api/story/sessions/review")).json()
            assert snapshot["turn_number"] == 0

    asyncio.run(run())
