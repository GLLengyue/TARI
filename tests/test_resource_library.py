import json
import struct
import zlib

import pytest

from trpg_runtime.resource_library import ResourceLibrary


def _png_with_card(card: dict) -> bytes:
    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + ctype
            + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    text = b"chara\x00" + json.dumps(card).encode()
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"tEXt", text)
        + chunk(b"IEND", b"")
    )


def test_scan_finds_builtin_resources():
    lib = ResourceLibrary()
    lib.scan()
    assert any(r.kind == "scenarios" and "station_zero" in r.id for r in lib.all())
    assert any(r.kind == "cards" and "mobi" in r.id for r in lib.all())
    assert any(r.kind == "cards" and "naifuer" in r.id for r in lib.all())
    assert any(r.kind == "worlds" and "wangushi" in r.id for r in lib.all())
    assert any(r.kind == "worlds" and "the-lighthouse" in r.id for r in lib.all())


def test_world_cards_classified_as_worlds_and_deduplicated():
    lib = ResourceLibrary()
    lib.scan()
    cards = lib.by_kind("cards")
    worlds = lib.by_kind("worlds")
    # 晚孤市 / Lighthouse / Redline are world cards, not narrow character cards.
    assert not any("wangushi" in r.id for r in cards)
    assert not any("redline" in r.id for r in cards)
    assert not any("lighthouse" in r.id for r in cards)
    # Fantasy character cards must stay cards even when they carry
    # genre tags like "架空世界".
    assert any("naifuer" in r.id for r in cards)
    # Their standalone lorebook JSONs and generated scenario YAMLs are covered
    # by the card worlds, so they must not create duplicate entries.
    assert not any("world-info" in r.id for r in worlds)
    assert not any("foreverse/scenarios" in r.id for r in worlds)
    assert sum(1 for r in worlds if "wangushi" in r.id) == 1
    # Intentional deduplication is not a scan warning.
    assert not any("duplicate world" in w for w in lib.warnings)


def test_card_png_and_json_dedupe_prefers_png(tmp_path):
    # Build a fake card JSON + minimal PNG-like file pair; the library should
    # register only one card entry and prefer the PNG as avatar.
    (tmp_path / "cards").mkdir()
    card = {"name": "Test Char", "description": "hello", "first_mes": "hi"}
    (tmp_path / "cards" / "test-char.json").write_text(json.dumps(card), encoding="utf-8")
    (tmp_path / "cards" / "test-char.png").write_bytes(_png_with_card(card))
    lib = ResourceLibrary([tmp_path])
    lib.scan()
    cards = [r for r in lib.by_kind("cards") if "test-char" in r.id]
    assert len(cards) == 1
    assert cards[0].meta["avatar"] is True


def test_upload_rejects_bad_json_world(tmp_path):
    lib = ResourceLibrary([tmp_path])
    lib.scan()
    with pytest.raises(ValueError, match="world-info"):
        lib.save_upload("worlds", "bad.json", b"not json at all")
    with pytest.raises(ValueError, match="entries"):
        lib.save_upload("worlds", "bad.json", json.dumps({"name": "x"}).encode())


def test_upload_scenario_and_world_roundtrip(tmp_path):
    lib = ResourceLibrary([tmp_path], upload_root=tmp_path / "uploads")
    lib.scan()
    scenario = {
        "campaign_id": "uploaded-scenario",
        "seed": 1,
        "default_locale": "en",
        "localizations": {
            "en": {
                "title": "Uploaded",
                "opening": "Start.",
                "scene": {
                    "scene_id": "s",
                    "title": "S",
                    "location": "",
                    "public_facts": [],
                    "hidden_facts": [],
                },
                "player": {"player_id": "player", "name": "P", "description": "d"},
                "actor": {"actor_id": "a", "name": "A", "description": "d"},
                "story_framework": {"premise": "p"},
            }
        },
    }
    res = lib.save_upload(
        "scenarios", "uploaded.yaml", json.dumps(scenario).encode()
    )
    assert res.kind == "scenarios"

    world = {
        "name": "W",
        "entries": [{"id": 0, "keys": [], "content": "fact one", "constant": True}],
    }
    res = lib.save_upload("worlds", "w.json", json.dumps(world).encode())
    assert res.kind == "worlds"
    assert res.meta["entry_count"] == 1
