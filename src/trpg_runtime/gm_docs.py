from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import RunContext

from .domain import CampaignState

RULES_TEXT = """TARI PbtA Core Rules (MVP)

Checks use 2d6 with no modifiers or difficulty classes.
- 10 or higher: full success.
- 7 to 9: success with a cost.
- 6 or lower: failure.

Authority: the runtime owns dice, spotlight, and state commits. The GM proposes
adjudications; the runtime validates and executes them.

Spotlight: the current speaker owns the spotlight. The GM may propose who speaks
next; invalid proposals automatically return to the player.

State patches: the GM may only propose patches under scene., actors., or status
paths. Patches are atomic; a rejected patch rejects the whole turn.

Fiction-first: the player declares intent in the fiction. The GM converts the
intent into a check when consequences are uncertain, defines stakes before the
roll, then narrates consequences based on the resolved roll.
"""


@dataclass
class SearchHit:
    doc_id: str
    title: str
    snippet: str
    source: str


@dataclass
class Document:
    category: str
    doc_id: str
    title: str
    text: str


@dataclass
class GMDocDeps:
    """Dependencies passed to the GM's read-only tools for one agent run."""

    registry: DocumentRegistry
    state: CampaignState
    calls: list[dict[str, Any]] = field(default_factory=list)


def _tokenize(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9_]+", text.lower()))
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    for i in range(len(cjk) - 1):
        tokens.add(cjk[i] + cjk[i + 1])
    return tokens


class DocumentRegistry:
    """Small deterministic retrieval layer for the GM.

    Keyword search over registered documents with CJK bigram support. No
    embeddings: results are exact, cheap, and easy to audit.
    """

    def __init__(self) -> None:
        self._docs: list[Document] = []

    def add_document(self, category: str, doc_id: str, title: str, text: str) -> None:
        self._docs.append(Document(category=category, doc_id=doc_id, title=title, text=text))

    def search(self, category: str, query: str, limit: int = 5) -> list[SearchHit]:
        query_tokens = _tokenize(query)
        hits: list[SearchHit] = []
        for doc in self._docs:
            if doc.category != category:
                continue
            for line in doc.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                line_tokens = _tokenize(line)
                matched = query_tokens & line_tokens
                if matched:
                    hits.append(
                        SearchHit(
                            doc_id=doc.doc_id,
                            title=doc.title,
                            snippet=line[:300],
                            source=f"{category}:{doc.doc_id}",
                        )
                    )
            if len(hits) >= limit:
                break
        return hits[:limit]

    def render(self, hits: list[SearchHit]) -> str:
        if not hits:
            return "No matching entries found."
        return "\n".join(f"[{h.source}] {h.snippet}" for h in hits)


def build_registry(state: CampaignState) -> DocumentRegistry:
    """Build the GM's document set for one turn from static rules + campaign state."""
    reg = DocumentRegistry()
    reg.add_document("rules", "pbta-core", "PbtA Core Rules", RULES_TEXT)

    world_text = "\n".join(
        [f"Scene: {state.scene.title} ({state.scene.location})"]
        + [f"Public fact: {f}" for f in state.scene.public_facts]
        + [f"Hidden fact: {f}" for f in state.scene.hidden_facts]
        + [
            f"Actor {a.name} ({a.actor_id}) at {a.location}: {a.description}"
            for a in state.actors.values()
        ]
        + [f"Actor {a.name} goals: {', '.join(a.goals)}" for a in state.actors.values()]
    )
    reg.add_document("world", state.scene.scene_id, state.scene.title, world_text)

    sf = state.story_framework
    outline = "\n".join(
        [
            f"Premise: {sf.premise}",
            f"Required beats: {', '.join(sf.required_beats)}",
            f"Optional beats: {', '.join(sf.optional_beats)}",
            f"Forbidden revelations: {', '.join(sf.forbidden_revelations)}",
            f"Possible endings: {', '.join(sf.possible_endings)}",
        ]
    )
    reg.add_document("scenario", "story-framework", "Scenario Outline", outline)
    return reg


# --- GM read-only tools -------------------------------------------------------


def gm_search_rules(ctx: RunContext[GMDocDeps], query: str) -> str:
    """Search the rulebook for rules relevant to the current adjudication."""
    hits = ctx.deps.registry.search("rules", query)
    ctx.deps.calls.append(
        {
            "tool": "search_rules",
            "args": {"query": query},
            "sources": list(dict.fromkeys(h.source for h in hits)),
        }
    )
    return ctx.deps.registry.render(hits)


def gm_search_world(ctx: RunContext[GMDocDeps], query: str) -> str:
    """Search the world setting (scene facts, locations, actor profiles)."""
    hits = ctx.deps.registry.search("world", query)
    ctx.deps.calls.append(
        {
            "tool": "search_world",
            "args": {"query": query},
            "sources": list(dict.fromkeys(h.source for h in hits)),
        }
    )
    return ctx.deps.registry.render(hits)


def gm_get_character_card(ctx: RunContext[GMDocDeps], actor_id: str) -> str:
    """Load a character's public card (no secrets)."""
    actor = ctx.deps.state.actors.get(actor_id)
    if actor is None:
        return f"Unknown actor: {actor_id}"
    public = actor.model_copy(update={"secrets": []}).model_dump_json()
    ctx.deps.calls.append(
        {"tool": "get_character_card", "args": {"actor_id": actor_id}, "sources": [actor_id]}
    )
    return public


def gm_get_scenario_outline(ctx: RunContext[GMDocDeps]) -> str:
    """Return the scenario outline: premise, beats, forbidden revelations, endings."""
    hits = ctx.deps.registry.search("scenario", "premise beats revelations endings")
    ctx.deps.calls.append(
        {
            "tool": "get_scenario_outline",
            "args": {},
            "sources": list(dict.fromkeys(h.source for h in hits)),
        }
    )
    return ctx.deps.registry.render(hits)
