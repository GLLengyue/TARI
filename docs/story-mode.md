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
- an offline `FakeNarrativeAuthor` for deterministic tests and demos.

The Fake Author is deliberately not a production writing model. The next integration point is an OpenAI-compatible author that receives the current beat, identity projection, relevant facts, recent events, and the player's input, then returns a `NarrativeAuthorProposal`.

## Importing a source document

The current source compiler is deliberately conservative. It parses UTF-8 `.txt`/`.md`/`.markdown` files into chapters, hashes the original document and each chapter, records source evidence, and emits a source-preserving Story Bundle. It does **not** claim to infer characters, plot arcs, or semantic facts yet.

```bash
trpg story-import path/to/story.md --output runtime-data/story.yaml --story-id my-story
trpg story-new runtime-data/story.yaml --session-id my-story-demo
```

The generated bundle uses `optional_rules.compiler = deterministic_scaffold`, one beat per imported chapter, and a `continue` choice between chapters. A later local-model compiler can replace this scaffold while keeping the same source/evidence fields and runtime contract.
