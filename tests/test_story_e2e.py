from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

import trpg_runtime.narrative.workflow as workflow
from trpg_runtime.narrative import (
    NarrativeInput,
    NarrativeOrchestrator,
    OpenAINarrativeAuthor,
    PlayerIdentity,
    StoryStore,
    resolve_llm_settings,
)
from trpg_runtime.narrative.providers import _extract_json_object
from trpg_runtime.story import load_bundle


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_SOURCE = REPO_ROOT / "examples" / "story" / "lantern_gate.md"


def _make_mock_transport(replies: list[dict[str, Any]]) -> Any:
    import httpx

    sequence = iter(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            payload = next(sequence)
        except StopIteration:
            return httpx.Response(500, json={"error": "no more mock replies"})
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _mock_reply(narrative: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"narrative": narrative})
                }
            }
        ]
    }


@pytest.fixture
def story_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    db = tmp_path / "story.db"
    monkeypatch.setenv("TRPG_DB_PATH", str(db))
    yield db


def _run_mock_end_to_end(
    tmp_path: Path, db: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    monkeypatch.setenv("TARI_LLM_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("TARI_LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("TRPG_DB_PATH", str(db))

    bundle_path = tmp_path / "lantern.yaml"
    output_path, document, bundle = workflow.import_bundle(
        str(EXAMPLE_SOURCE),
        output=str(bundle_path),
        story_id="lantern-e2e",
        title="Lantern End-to-End",
    )
    assert output_path == bundle_path
    assert bundle_path.is_file()
    assert bundle.optional_rules["compiler"] == "deterministic_scaffold"
    assert document.title == "The Lantern Gate"

    settings = resolve_llm_settings()
    store = StoryStore(db)
    session_id = "lantern-e2e-" + str(int(time.time()))

    first_beat = bundle.first_beat
    first_choice = first_beat.choices[0]
    second_beat = bundle.beat(first_choice.next_beat_id)
    second_choice = second_beat.choices[0]
    third_beat = bundle.beat(second_choice.next_beat_id)

    author = OpenAINarrativeAuthor(
        settings,
        transport=_make_mock_transport(
            [
                _mock_reply("The gate opens and the visitor steps into the rain."),
                _mock_reply("The archive receives the visitor's name in silence."),
            ]
        ),
    )
    runtime = NarrativeOrchestrator(store, bundle, author)
    _, state = workflow.create_session(
        str(bundle_path),
        session_id=session_id,
        identity=PlayerIdentity(display_name="Ari", identity_type="visitor"),
        canon_policy="guided",
        author=author,
        store=store,
    )
    state, result = asyncio.run(
        runtime.process_turn(
            state,
            NarrativeInput(choice_id=first_choice.choice_id, input_mode="choice"),
            request_id="e2e-turn-1",
        )
    )
    state, second = asyncio.run(
        runtime.process_turn(
            state,
            NarrativeInput(choice_id=second_choice.choice_id, input_mode="choice"),
            request_id="e2e-turn-2",
        )
    )
    asyncio.run(author.aclose())
    return {
        "session": session_id,
        "final_beat": state.current_beat_id,
        "trust": state.variables["trust"],
        "revealed": sorted(state.revealed_fact_ids),
        "result_beat": result.narrative_beat_id,
        "second_beat": second.narrative_beat_id,
        "first_choice": first_choice.choice_id,
        "second_choice": second_choice.choice_id,
        "third_beat": third_beat.beat_id,
    }


def test_end_to_end_import_to_branch_with_mocked_llm(
    tmp_path: Path, story_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full mock end-to-end: import -> start -> two selections -> branch."""
    summary = _run_mock_end_to_end(tmp_path, story_database, monkeypatch)

    assert summary["first_choice"] == "continue-001"
    assert summary["second_choice"] == "continue-002"
    assert summary["result_beat"] == "lantern-e2e-chapter-002"
    assert summary["second_beat"] == "lantern-e2e-chapter-003"
    assert summary["final_beat"] == summary["third_beat"]
    assert summary["trust"] == 0
    assert summary["revealed"] == []

    store = StoryStore(story_database)
    branches = {row["branch_id"] for row in store.list_story_branches(summary["session"])}
    assert "main" in branches
    child = workflow.branch_session(summary["session"], "hesitation", store=store)
    assert child.branch_id == "hesitation"
    assert child.parent_branch_id == "main"
    branches = {row["branch_id"] for row in store.list_story_branches(summary["session"])}
    assert {"main", "hesitation"} <= branches


@pytest.mark.skipif(
    os.environ.get("TARI_E2E_LLM") != "1",
    reason=(
        "set TARI_E2E_LLM=1 and TARI_LLM_* / EVOT_LLM_* to run "
        "the real local LLM end-to-end test"
    ),
)
def test_end_to_end_import_to_branch_with_real_local_llm(
    tmp_path: Path, story_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real LLM end-to-end against the configured local endpoint.

    Set ``TARI_E2E_LLM=1`` and either ``TARI_LLM_*`` or the same
    ``EVOT_LLM_*`` variables evot already uses.
    """
    settings = resolve_llm_settings()
    if not settings.is_configured:
        pytest.skip("no LLM endpoint configured")
    bundle_path = tmp_path / "lantern.yaml"
    workflow.import_bundle(
        str(EXAMPLE_SOURCE), output=str(bundle_path), story_id="real-lantern"
    )
    bundle = load_bundle(bundle_path)
    if not bundle.first_beat.choices:
        pytest.skip("bundle has no first-beat choices")

    session_id = "real-lantern-" + str(int(time.time()))
    store = StoryStore(story_database)
    author = OpenAINarrativeAuthor(settings)
    runtime = NarrativeOrchestrator(store, bundle, author)
    _, state = workflow.create_session(
        str(bundle_path),
        session_id=session_id,
        identity=PlayerIdentity(display_name="Ari", identity_type="visitor"),
        canon_policy="guided",
        author=author,
        store=store,
    )
    state, result = asyncio.run(
        runtime.process_turn(
            state,
            NarrativeInput(
                choice_id=bundle.first_beat.choices[0].choice_id,
                input_mode="choice",
            ),
            request_id="real-1",
        )
    )
    asyncio.run(author.aclose())
    assert result.narrative
    assert result.choices
    workflow.branch_session(session_id, "real-branch", store=store)
    branches = {row["branch_id"] for row in store.list_story_branches(session_id)}
    assert {"main", "real-branch"} <= branches


def test_resolve_llm_settings_prefers_tari_and_supports_evot_openai() -> None:
    tari = resolve_llm_settings(
        {
            "TARI_LLM_PROVIDER": "openai",
            "TARI_LLM_BASE_URL": "http://tari.example/v1",
            "TARI_LLM_API_KEY": "tari-key",
            "TARI_LLM_MODEL": "tari-model",
        }
    )
    assert tari.provider == "openai"
    assert tari.base_url == "http://tari.example/v1"
    assert tari.api_key == "tari-key"
    assert tari.model == "tari-model"

    router = resolve_llm_settings(
        {
            "TARI_LLM_PROVIDER": "openrouter",
            "TARI_LLM_BASE_URL": "https://openrouter.ai/api/v1",
            "TARI_LLM_API_KEY": "router-key",
            "TARI_LLM_MODEL": "minimax/minimax-m3:free",
        }
    )
    assert router.provider == "openrouter"
    assert router.base_url == "https://openrouter.ai/api/v1"
    assert router.model == "minimax/minimax-m3:free"

    evot = resolve_llm_settings(
        {
            "EVOT_LLM_PROVIDER": "openai",
            "EVOT_LLM_OPENAI_PROTOCOL": "openai",
            "EVOT_LLM_OPENAI_BASE_URL": "http://evot.example/v1",
            "EVOT_LLM_OPENAI_API_KEY": "evot-key",
            "EVOT_LLM_OPENAI_MODEL": "evot-model,secondary-model",
        }
    )
    assert evot.provider == "evot-openai"
    assert evot.base_url == "http://evot.example/v1"
    assert evot.api_key == "evot-key"
    assert evot.model == "evot-model"


def test_extract_json_object_handles_markdown_fences_and_trailing_text() -> None:
    fenced = "```json\n" + json.dumps({"narrative": "ok", "choices": []}) + "\n```"
    parsed = _extract_json_object(fenced)
    assert parsed == {"narrative": "ok", "choices": []}

    prose = (
        "Some prose then " + json.dumps({"narrative": "ok", "choices": []}) + " trailing words."
    )
    parsed = _extract_json_object(prose)
    assert parsed["narrative"] == "ok"
