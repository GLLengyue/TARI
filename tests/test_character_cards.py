import base64
import json
import struct
import zlib

import yaml

from trpg_runtime.character_cards import (
    CardSidecar,
    build_actor,
    detect_locale,
    generate_scenario,
    parse_card,
    write_scenario_yaml,
)
from trpg_runtime.scenario import load_scenario


def _card_doc(name="Mira", description="A guarded security officer.", **overrides):
    data = {
        "name": name,
        "description": description,
        "personality": "guarded, observant",
        "first_mes": "*Mira watches you from the doorway.* Hello.",
        "creator": "Test",
        "character_version": "1.0",
        "tags": ["security", "mystery"],
        "alternate_greetings": ["*A different entrance.*"],
        "character_book": {
            "name": "Mira's Knowledge",
            "entries": {
                "0": {
                    "uid": 0,
                    "key": ["monitoring", "tamper"],
                    "content": "The monitoring system was deliberately tampered with.",
                    "constant": True,
                },
                "1": {
                    "uid": 1,
                    "key": ["corridor"],
                    "content": "Unknown footsteps occasionally sound in the corridor.",
                    "constant": False,
                },
            },
        },
        "extensions": {
            "tari": {
                "goals": ["Conceal how much she knows."],
                "attributes": {"trust_level": 1},
            }
        },
    }
    data.update(overrides)
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}


def _make_png(chunks: dict[str, str]) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, payload):
        return (
            struct.pack(">I", len(payload))
            + ctype
            + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    raw = b"\x00" + b"\xff\xff\xff"
    out = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw))
    for key, value in chunks.items():
        out += chunk(b"tEXt", key.encode() + b"\x00" + value.encode())
    return out + chunk(b"IEND", b"")


def test_parse_json_card_v2(tmp_path):
    p = tmp_path / "mira.json"
    p.write_text(json.dumps(_card_doc()), encoding="utf-8")
    card = parse_card(p)
    assert card["name"] == "Mira"
    assert card["character_book"]["entries"]["0"]["content"].startswith("The monitoring")


def test_parse_png_card_prefers_ccv3(tmp_path):
    v2 = base64.b64encode(json.dumps(_card_doc()).encode()).decode()
    v3_doc = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {"name": "Mira V3"}}
    v3 = base64.b64encode(json.dumps(v3_doc).encode()).decode()
    p = tmp_path / "mira.png"
    p.write_bytes(_make_png({"chara": v2, "ccv3": v3}))
    card = parse_card(p)
    assert card["name"] == "Mira V3"


def test_build_actor_maps_card_fields():
    card = _card_doc()
    actor = build_actor(card)
    assert actor.name == "Mira"
    assert "guarded, observant" in actor.description
    assert actor.goals == ["Conceal how much she knows."]
    assert actor.attributes["trust_level"] == 1
    assert len(actor.knowledge) == 2
    assert actor.knowledge[0].confidence == 1.0
    assert actor.knowledge[1].confidence == 0.8
    assert actor.secrets == []
    assert actor.attributes["card"]["tags"] == ["security", "mystery"]
    assert actor.attributes["card"]["alternate_greetings"] == ["*A different entrance.*"]


def test_sidecar_provides_gm_only_data():
    card = _card_doc()
    sc = CardSidecar(
        actor_id="mira",
        location="Station Zero control room",
        goals=["Leave the station alive."],
        secrets=["Mira inspected the dead camera feed before the player arrived."],
        attributes={"trust_level": 3},
        scene={"scene_id": "control-room", "location": "Station Zero control room"},
    )
    actor = build_actor(card, sc)
    assert actor.actor_id == "mira"
    assert actor.location == "Station Zero control room"
    assert actor.goals == ["Leave the station alive."]
    assert actor.secrets == ["Mira inspected the dead camera feed before the player arrived."]
    assert actor.attributes["trust_level"] == 3


def test_secrets_never_come_from_public_card():
    card = _card_doc()
    # Even if a card tries to smuggle secrets into tari extensions, we ignore them.
    card["data"]["extensions"]["tari"]["secrets"] = ["leaked"]
    actor = build_actor(card)
    assert actor.secrets == []


def test_locale_detection():
    assert detect_locale("Hello there.") == "en"
    assert detect_locale("你好，我是墨笔。", "欢迎来到我的世界。") == "zh"


def test_generated_scenario_roundtrip(tmp_path):
    card = _card_doc(
        name="墨笔",
        description="墨笔是一位经验丰富的角色设定师。",
        first_mes="我是墨笔，一个专门帮人塑造角色的设定师。",
    )
    sc = CardSidecar(
        secrets=["墨笔的隐藏身份"],
        scene={"hidden_facts": ["世界真相"]},
        locale="zh",
    )
    actor = build_actor(card, sc)
    scenario = generate_scenario(actor, card, sc, campaign_id="mobi", seed=42)
    p = tmp_path / "mobi.yaml"
    write_scenario_yaml(p, scenario)
    state = load_scenario(p, lang="zh")
    assert state.locale == "zh"
    assert state.campaign_id == "mobi"
    assert state.seed == 42
    assert state.opening.startswith("我是墨笔")
    assert state.actors["墨笔"].name == "墨笔"
    assert state.actors["墨笔"].secrets == ["墨笔的隐藏身份"]
    assert state.scene.hidden_facts == ["世界真相"]


def test_generated_scenario_zh_detected(tmp_path):
    card = _card_doc(
        name="墨笔",
        description="墨笔是一位经验丰富的角色设定师。",
        first_mes="我是墨笔，一个专门帮人塑造角色的设定师。",
    )
    actor = build_actor(card)
    scenario = generate_scenario(actor, card)
    assert scenario["default_locale"] == "zh"
    blob = yaml.safe_dump(scenario, allow_unicode=True)
    assert "墨笔" in blob
