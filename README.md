# Agentic TRPG Runtime

A small, typed, auditable TRPG runtime for experimenting with **Game Master + Actor** agent separation. The MVP uses PydanticAI for structured model calls and a deterministic, PbtA-inspired rules core.

## Why this project exists

A single LLM that acts as world, referee, narrator, and every character can quietly rewrite facts or seize narrative control. This runtime separates authority:

- **Player** decides the player character's intention.
- **GM Agent** proposes checks and world consequences.
- **Actor Agent** performs one NPC's dialogue and intended action.
- **Rules runtime** owns dice, permissions, spotlight, and state commits.
- **Event store** records how the world reached its current state.

## MVP rules

Checks use an intentionally minimal PbtA-style `2d6` result:

- `10+`: full success
- `7-9`: success with a cost
- `6 or less`: failure

There are no difficulty classes and no modifiers in this MVP.

## Features

- CLI play loop
- PydanticAI GM, Actor, and semantic Auditor adapters
- Fake agents for offline tests and demos
- Explicit spotlight ownership
- Actor-specific knowledge projection
- Deterministic seeded `2d6`
- Atomic state patches
- Append-only SQLite event log
- Campaign snapshots and resume
- Independent provider/model/agent YAML configuration
- Debug trace output without requiring it in normal play

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
pytest

trpg new examples/station_zero.yaml --fake
trpg play station-zero --fake --debug
```

For real cloud agents:

```bash
cp .env.example .env
export OPENAI_API_KEY=...
trpg new examples/station_zero.yaml
trpg play station-zero
```

Model identifiers are configured in `config/agents.yaml`. PydanticAI accepts provider-qualified model identifiers. Keep credentials in environment variables.

## Commands

```text
trpg new SCENARIO [--campaign-id ID] [--seed N] [--fake]
trpg play CAMPAIGN_ID [--debug] [--fake]
trpg inspect-state CAMPAIGN_ID [--all]
trpg inspect-events CAMPAIGN_ID
trpg replay CAMPAIGN_ID
```

Data is stored in `runtime-data/trpg.db` by default. Override with `TRPG_DB_PATH`.

## Architecture

```text
CLI
  -> TurnOrchestrator
       -> GM Agent proposal
       -> Rules validation
       -> DiceEngine (2d6)
       -> GM resolution proposal
       -> atomic state commit
       -> SpotlightManager
       -> Actor Agent proposal
       -> semantic audit
       -> public transcript
  -> SQLite EventStore + snapshots
```

The runtime core does not trust model prose as state. Structured patches are validated before an atomic commit.

See [docs/architecture.md](docs/architecture.md), [docs/protocol.md](docs/protocol.md), and [docs/security.md](docs/security.md).

## Current limitations

- One player, one scene, and one spotlighted NPC actor
- No combat system or full PbtA move catalog
- Semantic auditor quality depends on the configured model
- LLM text is not deterministic, even when dice are
- Replay verifies recorded dice and event ordering; it does not regenerate identical prose
- No web UI or SillyTavern adapter yet

## Roadmap

1. Multiple actor instances with separate knowledge views
2. First-class branches, performance-only regeneration, and rerolls
3. Native HTTP API and OpenAI-compatible adapter
4. SillyTavern client integration
5. Optional local KoboldCpp actor provider
6. Scenario packs and richer PbtA move definitions

## License

MIT
