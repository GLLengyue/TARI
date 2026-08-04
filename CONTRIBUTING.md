# Contributing

1. Create a feature branch.
2. Keep the domain and rules layers independent of model providers.
3. Add tests for every permission or state-transition change.
4. Run `pytest` and `ruff check .` before opening a pull request.
5. Do not commit API keys, private prompts, campaign databases, or debug traces.

## Design invariants

- Model output is a proposal, never authoritative state.
- Dice are generated only by `DiceEngine`.
- An actor receives only its projected knowledge view.
- Public output requires a valid spotlight token.
