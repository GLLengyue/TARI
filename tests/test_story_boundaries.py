from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

import trpg_runtime.narrative.workflow as workflow
from trpg_runtime.llm import LLMSettings
from trpg_runtime.narrative import PlayerIdentity
from trpg_runtime.narrative.providers import OpenAINarrativeAuthor

BUNDLE_PATH = "examples/story/lantern_gate.yaml"


def test_complete_json_requests_openai_json_mode() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"result":"ok"}'}},
                ]
            },
        )

    author = OpenAINarrativeAuthor(
        LLMSettings(
            provider="test",
            base_url="http://compiler.test/v1",
            api_key="test-key",
            model="test-model",
        ),
        transport=httpx.MockTransport(handler),
    )
    try:
        result = asyncio.run(author.complete_json([{"role": "user", "content": "Return JSON."}]))
    finally:
        asyncio.run(author.aclose())

    assert result == {"result": "ok"}
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert requests[0]["model"] == "test-model"


def test_local_json_request_disables_qwen_thinking() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok":true}'}}]},
        )

    author = OpenAINarrativeAuthor(
        LLMSettings(
            provider="local",
            base_url="http://127.0.0.1:8080/v1",
            api_key="no-key-needed",
            model="local-model",
        ),
        transport=httpx.MockTransport(handler),
    )
    try:
        result = asyncio.run(author.complete_json([{"role": "user", "content": "Return JSON."}]))
    finally:
        asyncio.run(author.aclose())

    assert result == {"ok": True}
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_workflow_default_store_honors_trpg_db_path(tmp_path: Path, monkeypatch: Any) -> None:
    database = tmp_path / "workflow.db"
    monkeypatch.setenv("TRPG_DB_PATH", str(database))

    _, state = workflow.create_session(
        BUNDLE_PATH,
        session_id="workflow-default-store",
        identity=PlayerIdentity(display_name="Ari", identity_type="visitor"),
    )

    assert state.session_id == "workflow-default-store"
    assert database.is_file()
    assert (
        workflow.StoryStore(database).load_story_snapshot(state.session_id).session_id
        == state.session_id
    )


def test_source_plan_prompt_normalises_index_whitespace() -> None:
    from trpg_runtime.story.decomposer import _source_plan_prompt, _source_structure_candidates

    lines = ["Preamble", "##   Chapter   One  ", "Body"]
    candidates = _source_structure_candidates(lines)
    prompt = _source_plan_prompt("Story", "a" * 64, lines, candidates)

    assert '"text": "## Chapter One"' in prompt[-1]["content"]
