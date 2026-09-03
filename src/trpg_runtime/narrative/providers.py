from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .author import NarrativeAuthor
from .domain import NarrativeAuthorProposal, NarrativeChoice, NarrativeStatePatch


@dataclass(frozen=True)
class LLMSettings:
    """Resolved OpenAI-compatible narrative-author configuration."""

    provider: str
    base_url: str
    api_key: str
    model: str
    timeout: float = 90.0
    temperature: float = 0.4
    max_tokens: int = 1200

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)


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


def resolve_llm_settings(env: Mapping[str, str] | None = None) -> LLMSettings:
    """Resolve TARI settings, then the active provider configured for evot.

    ``TARI_LLM_*`` always wins. If those variables are absent, the active
    ``EVOT_LLM_*`` provider in ``~/.evotai/evot.env`` or the process environment
    is used when it speaks the OpenAI Chat Completions protocol.
    """
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
    return LLMSettings(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
    )


_SYSTEM_PROMPT = (
    "You are the prose author for an auditable interactive-fiction runtime.\n"
    "Return exactly one JSON object and no Markdown. Its only required key is:\n"
    '{"narrative":"short scene prose"}\n'
    "Write 40-160 words in the story's established voice.\n"
    "The runtime, not you, owns beat transitions, choices, facts, state effects, "
    "source references, and terminal status. Do not invent any of those fields.\n"
    "Do not reveal author-only facts.\n"
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _extract_json_object(text: str) -> dict[str, Any]:
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
        return _extract_json_object(fence.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("author response contained no JSON object")
    return _extract_json_object(text[start : end + 1])


def _narrative_from_content(text: str) -> str:
    try:
        payload = _extract_json_object(text)
    except ValueError:
        cleaned = _THINK_RE.sub("", text).strip().strip("`").strip()
        if not cleaned:
            raise ValueError("author returned empty narrative")
        return cleaned
    narrative = str(payload.get("narrative") or "").strip()
    if not narrative:
        raise ValueError("author JSON did not include a narrative")
    return narrative


class OpenAINarrativeAuthor(NarrativeAuthor):
    """Author prose through an OpenAI Chat Completions compatible endpoint.

    The model writes prose only. Beat transitions, choices, facts, effects,
    source references, and terminal state are derived from the immutable Story
    Bundle and the runtime-resolved player choice.
    """

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

    async def _post(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        body = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
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
    def _message_content(payload: dict[str, Any]) -> str:
        try:
            message = payload["choices"][0]["message"]
            content = message.get("content")
            if not content:
                content = message.get("reasoning_content", "")
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("LLM response did not include a message body") from exc
        if isinstance(content, list):
            return "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        return str(content)

    async def _call(self, messages: list[dict[str, str]]) -> str:
        return self._message_content(await self._post(messages))

    @staticmethod
    def _prompt(
        bundle: Any,
        current_beat: Any,
        target_beat: Any,
        player_input: str,
        selected_choice: Any,
        recent_events: Any,
    ) -> str:
        selected = selected_choice.text if selected_choice is not None else "freeform/continue"
        recent_types = [str(event.get("type", "")) for event in list(recent_events)[-4:]]
        return (
            f"Story: {bundle.title} ({bundle.story_id})\n"
            f"Current beat: {current_beat.beat_id} — {current_beat.title}\n"
            f"Resolved target beat: {target_beat.beat_id} — {target_beat.title}\n"
            f"Target beat source text:\n{target_beat.narrative}\n"
            f"Player action: {player_input!r}\n"
            f"Resolved choice: {selected}\n"
            f"Recent event types: {recent_types}\n"
            "Write the next short narrative moment. Return only JSON with a narrative key."
        )

    async def generate(
        self,
        bundle: Any,
        state: Any,
        current_beat: Any,
        player_input: str,
        selected_choice: Any,
        recent_events: Any,
    ) -> NarrativeAuthorProposal:
        from ..story.bundle import StoryBeat

        if not isinstance(current_beat, StoryBeat):
            raise TypeError("OpenAINarrativeAuthor requires a StoryBeat")
        target = (
            bundle.beat(selected_choice.next_beat_id)
            if selected_choice is not None
            else current_beat
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": self._prompt(
                    bundle,
                    current_beat,
                    target,
                    player_input,
                    selected_choice,
                    recent_events,
                ),
            },
        ]
        narrative = _narrative_from_content(await self._call(messages))

        patches: list[NarrativeStatePatch] = []
        if player_input.strip():
            patches.append(
                NarrativeStatePatch(
                    operation="set",
                    path="variables.last_input",
                    new_value=player_input.strip(),
                    reason="record the player's latest intent",
                    proposed_by="author",
                )
            )
        if selected_choice is not None:
            patches.extend(
                NarrativeStatePatch(
                    operation=effect.operation,
                    path=effect.path,
                    new_value=effect.value,
                    reason=effect.reason,
                    proposed_by="author",
                )
                for effect in selected_choice.effects
            )
            patches.append(
                NarrativeStatePatch(
                    operation="set",
                    path="variables.last_choice",
                    new_value=selected_choice.choice_id,
                    reason="record the selected story exit",
                    proposed_by="author",
                )
            )

        return NarrativeAuthorProposal(
            narrative=narrative,
            narrative_beat_id=target.beat_id,
            next_beat_id=target.beat_id,
            advance_beat=selected_choice is not None,
            choices=[NarrativeChoice.from_spec(choice) for choice in target.choices],
            state_patches=patches,
            revealed_fact_ids=(list(selected_choice.reveal_fact_ids) if selected_choice else []),
            source_refs=list(target.source_refs),
            ended=target.terminal,
            debug={"author": "openai", "model": self.settings.model},
        )


__all__ = ["LLMSettings", "OpenAINarrativeAuthor", "resolve_llm_settings"]
