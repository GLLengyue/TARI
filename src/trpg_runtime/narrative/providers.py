from __future__ import annotations

import re
from typing import Any

from ..llm import (
    LLMSettings,
    OpenAICompatibleClient,
    extract_json_object,
    resolve_llm_settings,
)
from ..story.bundle import StoryBeat
from .author import NarrativeAuthor
from .domain import NarrativeAuthorProposal, NarrativeChoice, NarrativeStatePatch

_SYSTEM_PROMPT = (
    "You are the prose author for an auditable interactive-fiction runtime.\n"
    "Return exactly one JSON object and no Markdown. Its only required key is:\n"
    '{"narrative":"short scene prose"}\n'
    "Write 40-160 words in the story's established voice.\n"
    "The runtime, not you, owns beat transitions, choices, facts, state effects, "
    "source references, and terminal status. Do not invent any of those fields.\n"
    "Do not reveal author-only facts.\n"
)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _narrative_from_content(text: str) -> str:
    try:
        payload = extract_json_object(text)
    except ValueError:
        cleaned = _THINK_RE.sub("", text).strip().strip("`").strip()
        if not cleaned:
            raise ValueError("author returned empty narrative") from None
        return cleaned
    narrative = str(payload.get("narrative") or "").strip()
    if not narrative:
        raise ValueError("author JSON did not include a narrative")
    return narrative


class OpenAINarrativeAuthor(NarrativeAuthor):
    """Adapt shared OpenAI-compatible text generation to Story Mode."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        *,
        client: Any | None = None,
        transport: Any | None = None,
    ) -> None:
        self.settings = settings or resolve_llm_settings()
        self._client = OpenAICompatibleClient(
            self.settings,
            client=client,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, messages: list[dict[str, str]]) -> str:
        return await self._client.complete_text(messages)

    async def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Run generic text generation through the shared provider boundary."""
        return await self._client.complete_text(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Request JSON mode through the shared provider boundary."""
        return await self._client.complete_json(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

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
            debug={"author": "openai-compatible", "model": self.settings.model},
        )


__all__ = ["LLMSettings", "OpenAINarrativeAuthor", "resolve_llm_settings"]
