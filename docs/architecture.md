# Architecture

## Authority model

The runtime is authoritative. LLMs return typed proposals only.

```text
proposal -> schema validation -> permission validation -> patch validation -> commit
```

### GM authority

The GM may propose checks, public narration, actor observations, spotlight targets, and world-state patches. The GM cannot generate dice or decide the player's unspoken actions.

### Actor authority

The actor may speak, declare its own intended action, and express a private thought. It cannot commit world outcomes, assign spotlight, or see hidden facts outside its knowledge view.

### Rules authority

`DiceEngine`, `SpotlightManager`, and `StateValidator` are deterministic Python components. Their decisions are not delegated to a model.

## Persistence

SQLite contains append-only events plus JSON snapshots. A committed state mutation and its event records are written in one transaction. The current implementation snapshots after every completed turn for simplicity.

## Extension boundaries

- `domain/`: typed state and proposal schemas
- `rules/`: deterministic mechanics and authorization
- `agents/`: PydanticAI and fake model adapters
- `runtime/`: turn state machine and orchestration
- `storage/`: event and snapshot persistence
- `cli.py`: replaceable client adapter
