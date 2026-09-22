"""OpenAI-compatible transport and configuration shared by runtime contexts."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class LLMSettings:
    """Resolved OpenAI-compatible endpoint configuration."""

    provider: str
    base_url: str
    api_key: str
    model: str
    timeout: float = 90.0
    temperature: float = 0.4
    max_tokens: int = 1200
    thinking_level: str = "off"

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    def _chat_template_kwargs(self) -> dict[str, Any] | None:
        if self.thinking_level != "off":
            return {"enable_thinking": True, "thinking_level": self.thinking_level}
        if _is_local_endpoint(self.base_url):
            return {"enable_thinking": False}
        return None


def _clean(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'")


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = _clean(value)
    return values


def _resolve_environment(env: Mapping[str, str] | None) -> dict[str, str]:
    if env is not None:
        return dict(env)
    values = _read_env_file(Path.home() / ".evotai" / "evot.env")
    values.update(os.environ)
    return values


def _provider_names(env: Mapping[str, str]) -> list[str]:
    active = _clean(env.get("EVOT_LLM_PROVIDER")).lower()
    names: list[str] = []
    if active:
        names.append(active)
    for name in ("openai", "openrouter"):
        if name not in names:
            names.append(name)
    return names


def _normalise_base_url(value: str) -> str:
    base = value.rstrip("/")
    suffix = "/chat/completions"
    if base.endswith(suffix):
        base = base[: -len(suffix)]
    return base


def _is_local_endpoint(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return (
        address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified
    )


def resolve_llm_settings(env: Mapping[str, str] | None = None) -> LLMSettings:
    """Resolve TARI settings, then the active OpenAI-compatible evot provider."""
    values = _resolve_environment(env)
    tari_base = _clean(values.get("TARI_LLM_BASE_URL"))
    tari_model = _clean(values.get("TARI_LLM_MODEL"))

    if tari_base and tari_model:
        provider = _clean(values.get("TARI_LLM_PROVIDER")) or "openai"
        api_key = _clean(values.get("TARI_LLM_API_KEY")) or "sk-no-key"
        base_url = _normalise_base_url(tari_base)
        model = tari_model
    else:
        provider = ""
        api_key = ""
        base_url = ""
        model = ""
        for candidate in _provider_names(values):
            prefix = "EVOT_LLM_" + candidate.upper() + "_"
            protocol = _clean(values.get(prefix + "PROTOCOL")).lower()
            candidate_base = _clean(values.get(prefix + "BASE_URL"))
            candidate_model = _clean(values.get(prefix + "MODEL")).split(",", 1)[0].strip()
            if not candidate_base or not candidate_model:
                continue
            if protocol and protocol not in {"openai", "openai_chat"}:
                continue
            provider = "evot-" + candidate
            api_key = _clean(values.get(prefix + "API_KEY")) or "sk-no-key"
            base_url = _normalise_base_url(candidate_base)
            model = candidate_model
            break
        if not base_url or not model:
            provider = "openai"
            api_key = "ollama"
            base_url = "http://127.0.0.1:11434/v1"
            model = "qwen2.5:7b"

    try:
        timeout = float(_clean(values.get("TARI_LLM_TIMEOUT")) or "90")
    except ValueError:
        timeout = 90.0
    try:
        temperature = float(_clean(values.get("TARI_LLM_TEMPERATURE")) or "0.4")
    except ValueError:
        temperature = 0.4
    try:
        max_tokens = int(_clean(values.get("TARI_LLM_MAX_TOKENS")) or "1200")
    except ValueError:
        max_tokens = 1200
    thinking_level = _clean(values.get("TARI_LLM_THINKING_LEVEL")).lower() or "off"
    if thinking_level not in {"off", "low", "medium", "high"}:
        thinking_level = "off"
    return LLMSettings(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
        thinking_level=thinking_level,
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response."""
    text = _THINK_RE.sub("", text).strip()
    if not text:
        raise ValueError("author returned empty content")
    if text[0] == "{" and text[-1] == "}":
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"author JSON is not valid: {exc}") from exc
        if isinstance(loaded, dict):
            return loaded
        raise ValueError("author JSON must be an object")
    fence = _FENCE_RE.search(text)
    if fence is not None:
        return extract_json_object(fence.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("author response contained no JSON object")
    return extract_json_object(text[start : end + 1])


class OpenAICompatibleClient:
    """Minimal async Chat Completions client with injectable HTTP transport."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        *,
        client: Any | None = None,
        transport: Any | None = None,
    ) -> None:
        self.settings = settings or resolve_llm_settings()
        self._client = client
        self._owns_client = False
        if client is None and transport is not None:
            import httpx

            self._client = httpx.AsyncClient(
                base_url=self.settings.base_url.rstrip("/") + "/",
                timeout=self.settings.timeout,
                headers=self._headers(),
                transport=transport,
                trust_env=False,
            )
            self._owns_client = True

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + self.settings.api_key,
            "Content-Type": "application/json",
        }

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _post(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_tokens": self.settings.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        chat_template_kwargs = self.settings._chat_template_kwargs()
        if chat_template_kwargs is not None:
            body["chat_template_kwargs"] = chat_template_kwargs
        if self._client is not None:
            response = await self._client.post("chat/completions", json=body)
            response.raise_for_status()
            return response.json()

        import httpx

        async with httpx.AsyncClient(
            base_url=self.settings.base_url.rstrip("/") + "/",
            timeout=self.settings.timeout,
            headers=self._headers(),
            trust_env=False,
        ) as client:
            response = await client.post("chat/completions", json=body)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def message_content(payload: dict[str, Any]) -> str:
        try:
            message = payload["choices"][0]["message"]
            content = message.get("content")
            if not content:
                content = message.get("reasoning_content", "")
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("LLM response did not include a message body") from exc
        if isinstance(content, list):
            return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return str(content)

    @staticmethod
    def raise_if_truncated(
        payload: dict[str, Any], text: str, *, budget: int | None = None
    ) -> None:
        """Fail loudly when the provider stopped because it ran out of budget.

        A truncated body is worse than a failed call: it reads as a finished
        piece of prose, so it gets published, summarised and journalled into
        the ledger while silently missing its own ending.
        """
        if OpenAICompatibleClient.finish_reason(payload) != "length":
            return
        where = f"max_tokens={budget}" if budget is not None else "the configured token budget"
        raise ValueError(
            f"model output was truncated: hit {where} before finishing "
            f"(finish_reason=length, {len(text)} chars); raise TARI_LLM_MAX_TOKENS"
            f" or lower the requested length | tail={text[-200:]!r}"
        )

    @staticmethod
    def finish_reason(payload: dict[str, Any]) -> str:
        """Return the provider's stop reason, or "" when it is unavailable."""
        try:
            return str(payload["choices"][0].get("finish_reason") or "")
        except (KeyError, IndexError, TypeError):
            return ""

    async def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        payload = await self._post(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = self.message_content(payload)
        self.raise_if_truncated(payload, text, budget=max_tokens)
        return text

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        payload = await self._post(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        text = self.message_content(payload)
        reason = self.finish_reason(payload)
        self.raise_if_truncated(payload, text, budget=max_tokens)
        try:
            return extract_json_object(text)
        except ValueError as exc:
            # Carry the raw output: callers may log it, and a bare parse error
            # cannot distinguish a malformed response from an empty one.
            raise ValueError(f"{exc} | finish_reason={reason or '?'} | raw={text[:400]!r}") from exc


__all__ = [
    "LLMSettings",
    "OpenAICompatibleClient",
    "extract_json_object",
    "resolve_llm_settings",
]
