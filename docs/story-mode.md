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

The generated bundle uses `optional_rules.compiler = deterministic_scaffold`, one beat per imported chapter, and a `continue` choice between chapters. It is the lossless import path; it does not infer characters, plot arcs, or semantic facts.

## Compiling semantic story resources

To create semantic chapter cards, rolling story arcs, world knowledge, novel structures, and a runtime-ready Story Bundle, use `story-compile` with an OpenAI-compatible local or remote endpoint configured through `TARI_LLM_*` or evot's `EVOT_LLM_*` variables:

```bash
trpg story-compile path/to/story.md \
  --output-dir runtime-data/story-books/my-story \
  --story-id my-story \
  --title "My Story" \
  --publish-world-path runtime-data/story-books/my-story/world-info.json
```

The compiler is resumable. It first asks the model for a source structure plan, then hashes the source, stores the pipeline version and settings in `manifest.json`, and caches each chapter card, arc window, world card/merge, and structure. Re-running the same command reuses valid artifacts; use `--rebuild` after intentionally changing the source or pipeline settings. `--max-chapters`, `--parallelism`, `--chapter-chars`, `--window-chapters`, `--max-arc-chapters`, `--world-batch-chapters`, and `--volume-size` control bounded work and memory use. The original source is never modified; line-numbered plans, source hashes, evidence, and failure checkpoints remain in the workspace.

A workspace contains the original parsed source (`source.json`), `source_plan.json`, source-referenced chapter cards, rolling arcs, world knowledge Markdown, structure files, `bundle.yaml`, `world-info.json`, and normalized `entities.json`, `canon-facts.json`, and `relationships.json`. The generated Bundle keeps source references and evidence, while the generated world-info file is compatible with the existing TARI/SillyTavern world-info import path. The same operation is available without Typer through `trpg_runtime.narrative.compile_bundle(...)` or `trpg_runtime.story.compile_source(...)`.

## Story Mode HTTP vertical slice

The Story Mode HTTP API is separate from the traditional campaign API. `GET /api/resources` returns validated Story Bundles under the `stories` key — note that the bundled single-page console does not yet render that key or provide a Story play view, so today this surface is for API clients, scripts, and tests. The endpoints are:

- `GET /api/resources` — list available Story Bundles and other resources;
- `POST /api/story/sessions` — create an atomic SQLite-backed session;
- `GET /api/story/sessions/{session_id}` — read the current state (`branch_id` selects a branch);
- `GET /api/story/sessions/{session_id}/events` and `/branches` — inspect event history and timelines;
- `POST /api/story/sessions/{session_id}/turns` — process a choice, freeform action, or continue request;
- `POST /api/story/sessions/{session_id}/branches/{branch_id}` — fork the current main-branch snapshot.

Story turns use the offline fake author by default; set `fake: false` to use the configured OpenAI-compatible endpoint. A compiled bundle is discovered by the resource library when it is placed in a configured resource root, or when its workspace directory is added through `TARI_RESOURCE_DIRS`, for example `TARI_RESOURCE_DIRS=runtime-data/story-books/my-story trpg web`. This is an initial Story Mode API, not the completed SillyTavern/OpenAI-compatible client adapter from the long-term roadmap.

The current acceptance path is: `story-import` writes a deterministic scaffold, `story-compile` produces resumable semantic resources, `story-new` creates an atomic SQLite-backed session, `story-play --author llm` processes a choice through the configured endpoint, `story-branch` forks the committed snapshot, and the HTTP API exposes the same state/turn/event/branch contract without mutating the parent timeline.
