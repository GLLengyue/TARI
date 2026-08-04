import json

from typer.testing import CliRunner

from trpg_runtime.cli import app
from trpg_runtime.lorebook import apply_world_info, world_info_from_state
from trpg_runtime.scenario import load_scenario
from trpg_runtime.storage import EventStore


def test_export_structure():
    state = load_scenario("examples/station_zero.yaml")
    book = world_info_from_state(state)
    entries = book["entries"]
    # 2 public + 2 hidden + 1 knowledge
    assert len(entries) == 5
    comments = [e["comment"] for e in entries.values()]
    assert comments.count("tari:public") == 2
    assert comments.count("tari:hidden") == 2
    assert "tari:know:mira" in comments
    assert all(entry["constant"] for entry in entries.values())


def test_export_import_roundtrip():
    state = load_scenario("examples/station_zero.yaml")
    book = world_info_from_state(state)
    restored = apply_world_info(state, book)
    assert restored.scene.public_facts == state.scene.public_facts
    assert restored.scene.hidden_facts == state.scene.hidden_facts
    mira = restored.actors["mira"]
    assert any("deliberately tampered" in k.content for k in mira.knowledge)
    assert len(mira.knowledge) == 2  # original + imported


def test_import_foreign_lorebook_becomes_public_facts():
    state = load_scenario("examples/station_zero.yaml")
    book = {
        "name": "Foreign",
        "entries": {
            "0": {"uid": 0, "content": "The station has three reactors.", "constant": True},
            "1": {"uid": 1, "content": "Reactor B is leaking.", "constant": False},
        },
    }
    merged = apply_world_info(state, book)
    assert "The station has three reactors." in merged.scene.public_facts
    assert "Reactor B is leaking." in merged.scene.public_facts
    assert merged.scene.hidden_facts == state.scene.hidden_facts


def test_import_knowledge_for_unknown_actor_falls_back_to_public():
    state = load_scenario("examples/station_zero.yaml")
    book = {
        "entries": {
            "0": {
                "uid": 0,
                "content": "The airlock code is 7742.",
                "constant": True,
                "comment": "tari:know:ghost",
            }
        }
    }
    merged = apply_world_info(state, book)
    assert "The airlock code is 7742." in merged.scene.public_facts


def test_cli_new_with_world_info(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("TRPG_DB_PATH", str(db))
    scenario = tmp_path / "scenario.yaml"
    scenario.write_text(
        """
campaign_id: test-camp
seed: 7
default_locale: en
localizations:
  en:
    title: Test
    opening: Hello.
    scene:
      scene_id: s1
      title: S1
      location: Here
      public_facts: []
      hidden_facts: []
    player:
      player_id: player
      name: Player
      description: A player.
    actor:
      actor_id: mira
      name: Mira
      description: Guard.
      location: Here
      goals: []
      knowledge: []
      secrets: []
      attributes: {}
    story_framework:
      premise: Test.
      required_beats: []
      optional_beats: []
      forbidden_revelations: []
      possible_endings: []
""",
        encoding="utf-8",
    )
    book = tmp_path / "world.json"
    book.write_text(
        json.dumps(
            {
                "entries": {
                    "0": {
                        "uid": 0,
                        "content": "The reactor hums.",
                        "constant": True,
                        "comment": "tari:public",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["new", str(scenario), "--world-info", str(book)])
    assert result.exit_code == 0, result.output
    state = EventStore(db).load_snapshot("test-camp")
    assert "The reactor hums." in state.scene.public_facts
