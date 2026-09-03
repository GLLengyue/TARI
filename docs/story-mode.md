# Story Mode

Story Mode is the first interactive-narrative slice in TARI. It is intentionally separate from the existing TRPG `CampaignState` and `TurnOrchestrator` paths.

## Run the vertical slice in Python

```python
import asyncio

from trpg_runtime.narrative import FakeNarrativeAuthor, NarrativeOrchestrator, PlayerIdentity, StoryStore
from trpg_runtime.story import load_bundle

bundle = load_bundle("examples/story/lantern_gate.yaml")
store = StoryStore("runtime-data/story.db")
runtime = NarrativeOrchestrator(store, bundle, FakeNarrativeAuthor())
state = asyncio.run(runtime.start_session(
    PlayerIdentity(display_name="Ari", identity_type="visitor"),
    session_id="lantern-demo",
))
state, result = asyncio.run(runtime.process_turn(
    state,
    {"choice_id": "trust", "input_mode": "choice"},
    request_id="lantern-demo-1",
))
print(result.narrative)
```

The current slice provides:

- validated YAML/JSON `StoryBundle` files;
- canonical facts, source references, entities, arcs, beats, and choices;
- `embody`, `possess`, `visitor`, and `replacement` identity types;
- guided, strict, and sandbox canon-policy values;
- one narrative-author call per decision;
- narrow, validated author patches under `variables.*` and `relationship_values.*`;
- append-only story events and atomic story-turn snapshots;
- request-id idempotency;
- child branches that inherit parent history without mutating the parent snapshot;
- an offline `FakeNarrativeAuthor` for deterministic tests and demos;
- an `OpenAINarrativeAuthor` for OpenAI Chat Completions-compatible local endpoints;
- a Typer-free workflow API for import, session creation, turn processing, and branching;
- a short mock E2E test plus an opt-in real local-LLM E2E test.

The OpenAI-compatible author deliberately asks the model for prose only. The runtime derives the target beat, choices, facts, source references, terminal state, and declared choice effects from the immutable Story Bundle. This keeps malformed model output from changing the timeline.

`TARI_LLM_*` takes precedence. If it is unset, TARI reads the active OpenAI-compatible provider from `~/.evotai/evot.env` using evot's `EVOT_LLM_*` variables. An evot provider configured with the Anthropic protocol is not silently translated; configure OpenRouter explicitly with its OpenAI-compatible `/api/v1` endpoint when needed. For example:

```bash
trpg story-play runtime-data/story.yaml my-story-demo --author llm
```

The Fake Author is still the default for offline demos. The real local-LLM E2E test is intentionally short and opt-in:

```bash
TARI_E2E_LLM=1 pytest tests/test_story_e2e.py -q
```

## Importing a source document

The current source compiler is deliberately conservative. It parses UTF-8 `.txt`/`.md`/`.markdown` files into chapters, hashes the original document and each chapter, records source evidence, and emits a source-preserving Story Bundle. It does **not** claim to infer characters, plot arcs, or semantic facts yet.

```bash
trpg story-import path/to/story.md --output runtime-data/story.yaml --story-id my-story
trpg story-new runtime-data/story.yaml --session-id my-story-demo
```

The generated bundle uses `optional_rules.compiler = deterministic_scaffold`, one beat per imported chapter, and a `continue` choice between chapters. A later local-model compiler can replace this scaffold while keeping the same source/evidence fields and runtime contract.

The current acceptance path is: `story-import` writes a bundle, `story-new` creates an atomic SQLite-backed session, `story-play --author llm` processes one or more choices through the configured local endpoint, and `story-branch` forks the committed snapshot without mutating the parent.
