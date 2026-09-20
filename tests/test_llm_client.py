"""Shared LLM client: settings resolution and request body construction."""

from __future__ import annotations

import asyncio
import dataclasses
import json

import httpx

from trpg_runtime.llm.client import (
    LLMSettings,
    OpenAICompatibleClient,
    extract_json_object,
    resolve_llm_settings,
)

LOCAL_SETTINGS = LLMSettings(
    provider="test",
    base_url="http://192.168.1.87:8080/v1",
    api_key="sk-no-key",
    model="qwen3.8-27b",
)
HOSTED_SETTINGS = LLMSettings(
    provider="test",
    base_url="https://api.example.com/v1",
    api_key="unused",
    model="test-model",
)


def _capture_body(settings: LLMSettings) -> dict[str, object]:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    async def run() -> None:
        client = OpenAICompatibleClient(
            settings=settings,
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.complete_text([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

    asyncio.run(run())
    assert len(bodies) == 1
    return bodies[0]


def test_local_endpoint_without_thinking_disables_thinking() -> None:
    body = _capture_body(LOCAL_SETTINGS)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_local_endpoint_with_low_thinking_sends_template_kwargs() -> None:
    settings = dataclasses.replace(LOCAL_SETTINGS, thinking_level="low")
    body = _capture_body(settings)
    assert body["chat_template_kwargs"] == {
        "enable_thinking": True,
        "thinking_level": "low",
    }


def test_hosted_endpoint_without_thinking_sends_nothing() -> None:
    body = _capture_body(HOSTED_SETTINGS)
    assert "chat_template_kwargs" not in body


def test_explicit_thinking_level_is_sent_to_any_endpoint() -> None:
    settings = dataclasses.replace(HOSTED_SETTINGS, thinking_level="high")
    body = _capture_body(settings)
    assert body["chat_template_kwargs"] == {
        "enable_thinking": True,
        "thinking_level": "high",
    }


def test_resolve_llm_settings_reads_tari_variables_and_thinking_level() -> None:
    env = {
        "TARI_LLM_BASE_URL": "http://192.168.1.87:8080/v1",
        "TARI_LLM_API_KEY": "sk-local",
        "TARI_LLM_MODEL": "qwen3.8-27b",
        "TARI_LLM_TIMEOUT": "900",
        "TARI_LLM_MAX_TOKENS": "4096",
        "TARI_LLM_THINKING_LEVEL": "LOW",
    }
    settings = resolve_llm_settings(env)
    assert settings.base_url == "http://192.168.1.87:8080/v1"
    assert settings.model == "qwen3.8-27b"
    assert settings.timeout == 900.0
    assert settings.max_tokens == 4096
    assert settings.thinking_level == "low"


def test_resolve_llm_settings_invalid_thinking_level_falls_back_to_off() -> None:
    env = {
        "TARI_LLM_BASE_URL": "http://192.168.1.87:8080/v1",
        "TARI_LLM_MODEL": "qwen3.8-27b",
        "TARI_LLM_THINKING_LEVEL": "ultra",
    }
    settings = resolve_llm_settings(env)
    assert settings.thinking_level == "off"


def test_resolve_llm_settings_defaults_thinking_level_off() -> None:
    env = {"TARI_LLM_BASE_URL": "http://127.0.0.1:11434/v1", "TARI_LLM_MODEL": "qwen2.5:7b"}
    assert resolve_llm_settings(env).thinking_level == "off"


def test_extract_json_object_strips_thinking_blocks_and_fences() -> None:
    text = '思考过程\n```json\n{"narrative": "A scene."}\n```'
    assert extract_json_object(text) == {"narrative": "A scene."}
