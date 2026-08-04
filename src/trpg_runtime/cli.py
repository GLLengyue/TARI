from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from .agents import FakeAgentSuite, PydanticAISuite
from .config import load_runtime_config
from .runtime import TurnOrchestrator
from .scenario import load_scenario
from .storage import EventStore

app = typer.Typer(help="Agentic TRPG Runtime")
console = Console()


def store():
    return EventStore(os.getenv("TRPG_DB_PATH", "runtime-data/trpg.db"))


def suite(fake: bool):
    return FakeAgentSuite() if fake else PydanticAISuite(load_runtime_config("config/agents.yaml"))


@app.command("new")
def new_campaign(scenario: str, campaign_id: str | None = None, seed: int | None = None, fake: bool = False):
    state = load_scenario(scenario, campaign_id, seed)
    s = store()
    s.append(state.campaign_id, 0, "campaign_created", {"title": state.title, "seed": state.seed})
    s.save_snapshot(state)
    console.print(Panel(state.opening, title=state.title))
    console.print(f"Campaign created: [bold]{state.campaign_id}[/bold]\nSeed: {state.seed}\nSpotlight: PLAYER")


@app.command("play")
def play(campaign_id: str, debug: bool = False, fake: bool = False):
    s = store()
    state = s.load_snapshot(campaign_id)
    runtime = TurnOrchestrator(s, suite(fake))
    console.print(Panel(state.opening, title=f"{state.title} | turn {state.turn_number}"))
    while True:
        text = console.input("[bold cyan]> [/bold cyan]").strip()
        if text in {"/quit", "/exit"}:
            break
        if not text:
            continue
        try:
            state, result = asyncio.run(runtime.process_turn(state, text))
        except Exception as exc:
            console.print(f"[red]Turn failed without advancing state: {exc}[/red]")
            continue
        if result.roll:
            console.print(f"[yellow][Roll] {result.roll.rolls[0]} + {result.roll.rolls[1]} = {result.roll.total} -> {result.roll.outcome.value}[/yellow]")
        if result.gm_narration:
            console.print(Panel(result.gm_narration, title="GM"))
        if result.actor_action or result.actor_speech:
            body = "\n".join(x for x in [result.actor_action, result.actor_speech] if x)
            console.print(Panel(body, title="Actor"))
        if debug:
            console.print_json(json.dumps(result.debug, default=str))


@app.command("inspect-state")
def inspect_state(campaign_id: str, all: bool = typer.Option(False, "--all")):
    state = store().load_snapshot(campaign_id)
    data = state.model_dump(mode="json")
    if not all:
        data["scene"].pop("hidden_facts", None)
        for actor in data["actors"].values():
            actor.pop("secrets", None)
            actor.pop("knowledge", None)
        data.pop("story_framework", None)
    console.print_json(json.dumps(data, default=str))


@app.command("inspect-events")
def inspect_events(campaign_id: str):
    console.print_json(json.dumps(store().events(campaign_id), default=str))


@app.command("replay")
def replay(campaign_id: str):
    events = store().events(campaign_id)
    for e in events:
        if e["type"] in {"dice_rolled", "public_narrative_emitted", "turn_completed"}:
            console.print(f"{e['seq']:04d} turn={e['turn']} {e['type']}: {e['payload']}")


if __name__ == "__main__":
    app()
