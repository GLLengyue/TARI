from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.panel import Panel

from .agents import FakeAgentSuite, PydanticAISuite
from .character_cards import (
    CardSidecar,
    build_actor,
    detect_locale,
    generate_scenario,
    parse_card,
    write_scenario_yaml,
)
from .config import load_runtime_config
from .domain import SpotlightOwner, SpotlightToken, StatePatch
from .i18n import DEFAULT_LOCALE, t
from .lorebook import apply_world_info, world_info_from_state
from .narrative import (
    CanonPolicy,
    FakeNarrativeAuthor,
    NarrativeInput,
    NarrativeOrchestrator,
    PlayerIdentity,
    StoryStore,
)
from .rules import apply_patches
from .runtime import TurnOrchestrator
from .scenario import load_scenario
from .storage import EventStore
from .story import load_bundle, parse_source, scaffold_bundle, write_bundle

app = typer.Typer(help="Agentic TRPG Runtime")
console = Console()


def _load_env_file(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE pairs from .env without overriding existing env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()


def store():
    return EventStore(os.getenv("TRPG_DB_PATH", "runtime-data/trpg.db"))


def suite(fake: bool, locale: str = DEFAULT_LOCALE):
    return (
        FakeAgentSuite(locale)
        if fake
        else PydanticAISuite(load_runtime_config("config/agents.yaml"), locale)
    )


@app.command("new")
def new_campaign(
    scenario: str,
    campaign_id: str | None = None,
    seed: int | None = None,
    fake: bool = False,
    lang: str | None = None,
    world_info: str | None = None,
):
    state = load_scenario(scenario, campaign_id, seed, lang=lang)
    if world_info:
        book = json.loads(Path(world_info).read_text(encoding="utf-8"))
        state = apply_world_info(state, book)
    s = store()
    s.append(state.campaign_id, 0, "campaign_created", {"title": state.title, "seed": state.seed})
    s.save_snapshot(state)
    console.print(Panel(state.opening, title=state.title))
    console.print(t(state.locale, "campaign_created", id=state.campaign_id))
    console.print(t(state.locale, "seed", seed=state.seed))
    console.print(t(state.locale, "spotlight", owner=state.spotlight.owner_type.value))


@app.command("export-lorebook")
def export_lorebook(campaign_id: str, output: str | None = None):
    """Export a campaign's facts and actor knowledge as a SillyTavern world-info file."""
    state = store().load_snapshot(campaign_id)
    book = world_info_from_state(state)
    out_path = Path(output or f"{state.campaign_id}.worldinfo.json")
    out_path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"World info written: [bold]{out_path}[/bold] ({len(book['entries'])} entries)")


@app.command("import-card")
def import_card(
    card_path: str,
    output: str | None = None,
    sidecar: str | None = None,
    seed: int | None = None,
    campaign_id: str | None = None,
    show: bool = False,
):
    """Import a SillyTavern Character Card (PNG/JSON, V2/V3) as a TARI scenario."""
    card = parse_card(card_path)
    sc = None
    if sidecar:
        sc = CardSidecar.model_validate(yaml.safe_load(Path(sidecar).read_text(encoding="utf-8")))
    actor = build_actor(card, sc)
    if show:
        console.print(Panel(actor.description or actor.name, title=actor.name))
        console.print(
            f"Actor id: [bold]{actor.actor_id}[/bold] | location: {actor.location or 'n/a'}"
        )
        locale_hint = detect_locale(actor.description, str(card.get("first_mes") or ""))
        console.print(
            f"Goals: {len(actor.goals)} | Knowledge: {len(actor.knowledge)} | "
            f"Secrets: {len(actor.secrets)} | Locale: {locale_hint}"
        )
        if card.get("tags"):
            console.print(f"Tags: {', '.join(card['tags'])}")
        return
    scenario = generate_scenario(actor, card, sc, campaign_id=campaign_id, seed=seed)
    out_path = Path(output or f"{scenario['campaign_id']}.yaml")
    write_scenario_yaml(out_path, scenario)
    console.print(f"Scenario written: [bold]{out_path}[/bold]")
    console.print(
        f"Campaign id: {scenario['campaign_id']} | seed: {scenario['seed']} | "
        f"locale: {scenario['default_locale']}"
    )
    console.print(
        f"Next: trpg new {out_path} --lang {scenario['default_locale']} && "
        f"trpg play {scenario['campaign_id']}"
    )


@app.command("play")
def play(campaign_id: str, debug: bool = False, fake: bool = False, progress: bool = True):
    s = store()
    state = s.load_snapshot(campaign_id)
    locale = state.locale

    def on_progress(stage, payload):
        if stage == "dice":
            outcome = t(locale, f"outcome.{payload['outcome']}")
            roll_text = t(
                locale,
                "roll",
                a=payload["rolls"][0],
                b=payload["rolls"][1],
                total=payload["total"],
                outcome=outcome,
            )
            console.print(f"[yellow]{roll_text}[/yellow]")
            return
        label = (
            t(locale, f"progress.{stage}")
            if stage
            in {
                "gm_planning",
                "rolling",
                "gm_resolving",
                "committing",
                "actor_turn",
                "auditing",
                "completed",
            }
            else None
        )
        if label:
            console.print(f"[dim]{label}[/dim]")

    runtime = TurnOrchestrator(
        s, suite(fake, locale), on_progress=on_progress if progress else None
    )
    console.print(
        Panel(
            state.opening, title=f"{state.title} | {t(locale, 'turn_title', n=state.turn_number)}"
        )
    )
    while True:
        text = console.input("[bold cyan]> [/bold cyan]").strip()
        if text in {"/quit", "/exit"}:
            break
        if not text:
            continue
        try:
            state, result = asyncio.run(
                runtime.process_turn(state, text, request_id=str(uuid.uuid4()))
            )
        except Exception as exc:
            console.print(f"[red]{t(locale, 'turn_failed', error=exc)}[/red]")
            continue
        if result.roll and not progress:
            outcome = t(locale, f"outcome.{result.roll.outcome.value}")
            roll_text = t(
                locale,
                "roll",
                a=result.roll.rolls[0],
                b=result.roll.rolls[1],
                total=result.roll.total,
                outcome=outcome,
            )
            console.print(f"[yellow]{roll_text}[/yellow]")
        if result.gm_narration:
            console.print(Panel(result.gm_narration, title=t(locale, "gm")))
        if result.actor_action or result.actor_speech:
            body = "\n".join(x for x in [result.actor_action, result.actor_speech] if x)
            console.print(Panel(body, title=t(locale, "actor")))
        if debug:
            console.print_json(json.dumps(result.debug, default=str))


def story_store() -> StoryStore:
    return StoryStore(os.getenv("TRPG_DB_PATH", "runtime-data/trpg.db"))


def _story_policy(value: str) -> CanonPolicy:
    try:
        return CanonPolicy(value)
    except ValueError as exc:
        choices = ", ".join(policy.value for policy in CanonPolicy)
        raise typer.BadParameter(f"must be one of: {choices}") from exc


def _story_prompt(bundle, state) -> str:
    if state.turn_number == 0:
        return state.last_narrative + "\n\n" + bundle.beat(state.current_beat_id).narrative
    return state.last_narrative


def _print_story_choices(state) -> None:
    if not state.available_choices:
        console.print("[dim]No choices are available. Type freeform text or /continue.[/dim]")
        return
    console.print("[bold]Choices:[/bold]")
    for index, choice in enumerate(state.available_choices, start=1):
        console.print(f"  {index}. [{choice.choice_id}] {choice.text} ({choice.risk})")


def _story_input_from_text(state, text: str) -> NarrativeInput:
    for index, choice in enumerate(state.available_choices, start=1):
        if text == choice.choice_id or text == str(index):
            return NarrativeInput(choice_id=choice.choice_id, input_mode="choice")
    if text == "/continue":
        return NarrativeInput(input_mode="continue")
    return NarrativeInput(text=text, input_mode="freeform")


@app.command("story-import")
def story_import(
    source: str,
    output: str | None = None,
    story_id: str | None = None,
    title: str | None = None,
    lang: str = "en",
    max_chapters: int | None = None,
):
    """Compile a TXT/Markdown source into a source-preserving Story Bundle scaffold."""
    try:
        document = parse_source(
            source,
            source_id=story_id,
            title=title,
            locale=lang,
            max_chapters=max_chapters,
        )
        bundle = scaffold_bundle(document, story_id=story_id, title=title)
        output_path = Path(output or (bundle.story_id + ".yaml"))
        write_bundle(output_path, bundle)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Story Bundle written: [bold]{output_path}[/bold]")
    console.print(
        f"Source: {document.title} | chapters: {len(document.chapters)} | "
        f"compiler: {bundle.optional_rules['compiler']}"
    )
    console.print(
        f"Next: trpg story-new {output_path} --session-id {bundle.story_id}-demo"
    )


@app.command("story-new")
def story_new(
    bundle: str,
    session_id: str | None = None,
    player_name: str = "Player",
    identity_type: str = "visitor",
    persona: str = "",
    host_character: str | None = None,
    seed: int = 0,
    canon_policy: str = "guided",
):
    """Create an offline interactive-narrative session from a Story Bundle."""
    story_bundle = load_bundle(bundle)
    try:
        identity = PlayerIdentity(
            display_name=player_name,
            identity_type=identity_type,
            persona=persona,
            host_character=host_character,
        )
    except ValueError as exc:
        raise typer.BadParameter(f"invalid player identity: {exc}") from exc
    runtime = NarrativeOrchestrator(
        story_store(), story_bundle, FakeNarrativeAuthor()
    )
    state = asyncio.run(
        runtime.start_session(
            identity,
            session_id=session_id,
            seed=seed,
            canon_policy=_story_policy(canon_policy),
        )
    )
    console.print(Panel(_story_prompt(story_bundle, state), title=state.title))
    console.print(
        f"Session created: [bold]{state.session_id}[/bold] | branch: {state.branch_id}"
    )
    _print_story_choices(state)
    console.print(
        f"Next: trpg story-play {bundle} {state.session_id} --branch-id {state.branch_id}"
    )


@app.command("story-play")
def story_play(bundle: str, session_id: str, branch_id: str = "main"):
    """Play a Story Bundle session with the deterministic offline author."""
    story_bundle = load_bundle(bundle)
    s = story_store()
    try:
        state = s.load_story_snapshot(session_id, branch_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    runtime = NarrativeOrchestrator(s, story_bundle, FakeNarrativeAuthor())

    console.print(Panel(_story_prompt(story_bundle, state), title=state.title))
    _print_story_choices(state)
    while True:
        if state.status == "completed":
            console.print("[bold green]Story completed.[/bold green]")
            break
        text = console.input("[bold cyan]story> [/bold cyan]").strip()
        if text in {"/quit", "/exit"}:
            break
        if text.startswith("/branch "):
            new_branch_id = text.partition(" ")[2].strip()
            try:
                state = runtime.fork(state, new_branch_id)
            except Exception as exc:
                console.print(f"[red]Branch failed: {exc}[/red]")
                continue
            console.print(
                f"Switched to branch [bold]{state.branch_id}[/bold] from turn {state.turn_number}."
            )
            _print_story_choices(state)
            continue
        if not text:
            continue
        incoming = _story_input_from_text(state, text)
        try:
            state, result = asyncio.run(
                runtime.process_turn(state, incoming, request_id=str(uuid.uuid4()))
            )
        except Exception as exc:
            console.print(f"[red]Story turn failed: {exc}[/red]")
            continue
        console.print(Panel(result.narrative, title=f"Turn {result.turn_number}"))
        _print_story_choices(state)


@app.command("story-branch")
def story_branch(
    session_id: str,
    branch_id: str,
    from_branch: str = "main",
):
    """Create a child Story Mode branch without starting a play loop."""
    s = story_store()
    try:
        parent = s.load_story_snapshot(session_id, from_branch)
        child = s.create_story_branch(parent, branch_id)
    except (KeyError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        "Created story branch [bold]"
        + child.branch_id
        + "[/bold] from "
        + child.parent_branch_id
        + " at turn "
        + str(child.turn_number)
        + "."
    )


@app.command("web")
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Listen address."),
    port: int = typer.Option(8765, "--port", help="Listen port."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the browser."),
):
    """Start the local web console (FastAPI + static frontend)."""
    import webbrowser

    import uvicorn

    host = os.getenv("TARI_WEB_HOST", host)
    port = int(os.getenv("TARI_WEB_PORT", port))
    console.print(f"TARI web console: http://{host}:{port}/")
    if host in ("0.0.0.0", "::"):
        console.print(
            "[yellow]Listening on all interfaces: anyone on the network can reach this "
            "console. Use 127.0.0.1 unless you intend that.[/yellow]"
        )
    if open_browser:
        browser_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        webbrowser.open(f"http://{browser_host}:{port}/")
    from trpg_runtime.web.app import create_app

    uvicorn.run(create_app(), host=host, port=port)


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
        if e["type"] in {
            "dice_rolled",
            "public_narrative_emitted",
            "turn_completed",
            "turn_aborted",
        }:
            prefix = "[red]" if e["type"] == "turn_aborted" else ""
            suffix = "[/red]" if e["type"] == "turn_aborted" else ""
            console.print(
                f"{prefix}{e['seq']:04d} turn={e['turn']} {e['type']}: {e['payload']}{suffix}"
            )


@app.command("recover")
def recover(campaign_id: str, scenario: str):
    """Rebuild a campaign snapshot by replaying committed events from a scenario file."""
    s = store()
    events = s.events(campaign_id)
    created = [e for e in events if e["type"] == "campaign_created"]
    if not created:
        console.print("[red]No campaign_created event found; cannot recover.[/red]")
        raise typer.Exit(1)
    seed = created[0]["payload"].get("seed")
    state = load_scenario(scenario, campaign_id=campaign_id, seed=seed)
    patched = 0
    last_turn = 0
    for e in events:
        if e["type"] == "state_patch_committed":
            patches = [StatePatch.model_validate(p) for p in e["payload"]["patches"]]
            state = apply_patches(state, patches)
            patched += 1
            last_turn = max(last_turn, e["turn"])
        elif e["type"] == "turn_completed":
            last_turn = max(last_turn, e["turn"])
    state.turn_number = last_turn
    state.spotlight = SpotlightToken(
        owner_type=SpotlightOwner.PLAYER,
        owner_id=state.player.player_id,
        scopes={"own_action"},
        granted_at_turn=last_turn,
        reason="rebuilt snapshot",
    )
    s.append(
        campaign_id,
        last_turn,
        "snapshot_rebuilt",
        {"version": state.version, "turn": last_turn, "patches": patched},
    )
    s.save_snapshot(state)
    console.print(
        f"Recovered [bold]{campaign_id}[/bold]: turn {last_turn}, version {state.version}, "
        f"{patched} patch groups replayed"
    )


if __name__ == "__main__":
    app()
