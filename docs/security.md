# Security and privacy

- API keys are read from environment variables (`.env`, `TARI_LLM_*`, or the active evot provider) and must never be committed.
- Agent debug traces can contain scenario secrets and private character knowledge.
- Normal CLI output omits hidden facts and private thoughts.
- SQLite campaign files may contain unpublished story material.

## Local HTTP console

`trpg web` currently exposes three groups of endpoints on one unauthenticated FastAPI app:

- `/api/campaigns/...` — create, read, and advance campaigns;
- `/api/story/...` — create Story sessions, advance turns, read events, and fork branches;
- `/api/resources/...` and `/api/settings` — list/upload material files and rewrite `config/agents.yaml`.

The default bind address is `127.0.0.1`, so the blast radius is the local machine. Treat any other bind address as a deployment decision, not a convenience flag:

- `--host 0.0.0.0` (or a non-loopback `TARI_WEB_HOST`) puts campaign state, uploaded cards, world books, and the ability to overwrite the agent config file on the local network. The CLI prints a warning, but there is no authentication, authorization, per-user ownership, rate limiting, or request size limit behind it.
- Story `author_only` facts and Campaign hidden facts stay out of the normal transcript responses today, but they are present in the underlying SQLite file and in `inspect-state --all`. Anyone who can read the database can read the secrets.
- Request bodies are parsed as JSON without a size cap; upload endpoints accept files into the configured resource roots. Do not run this console on a shared or untrusted network.

## Before public exposure

Shipping this console beyond localhost requires, at minimum (tracked as the M3 security gate in the roadmap):

1. authentication and per-session/per-branch resource ownership checks;
2. rate limiting, request size limits, timeouts, and cancellation;
3. an explicit read-only mode, or removal of `PUT /api/settings` and the upload endpoints;
4. TLS termination and secret-free logging (no prompts, no provider keys, no chain-of-thought in access logs);
5. confirmation that public transcript responses cannot leak hidden or `author_only` facts.

- A cloud-agent configuration sends the projected prompt content to the configured provider. Review provider data-handling terms before using private material. The same applies to `story-compile`: the full source text is sent to whatever endpoint is configured. The default configuration resolves to a local or private endpoint; verify the endpoint before compiling unpublished work.
