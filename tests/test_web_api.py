import asyncio
import json
import shutil

import httpx

from trpg_runtime.agents import FakeAgentSuite
from trpg_runtime.resource_library import ResourceLibrary
from trpg_runtime.web.app import create_app


def _make_client(tmp_path, library=None, agent_factory=None):
    db = tmp_path / "test.db"
    config = tmp_path / "agents.yaml"
    shutil.copy("config/agents.yaml", config)
    app = create_app(
        db_path=str(db),
        config_path=str(config),
        library=library,
        agent_factory=agent_factory or (lambda fake, locale: FakeAgentSuite(locale)),
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


def test_resources_endpoint_lists_everything(tmp_path):
    async def run():
        async with _make_client(tmp_path) as client:
            res = await client.get("/api/resources")
            assert res.status_code == 200
            data = res.json()
            assert data["scenarios"]
            assert data["cards"]
            assert data["worlds"]
            assert any(r["id"] == "pbta-minimal" for r in data["rulesets"])

    asyncio.run(run())


def test_preview_and_campaign_flow(tmp_path):
    async def run():
        async with _make_client(tmp_path) as client:
            resources = (await client.get("/api/resources")).json()
            card_id = next(r["id"] for r in resources["cards"] if "mobi" in r["id"])

            preview = await client.post(
                "/api/resources/preview",
                json={"card_id": card_id, "lang": "zh"},
            )
            assert preview.status_code == 200
            assert preview.json()["locale"] == "zh"

            created = await client.post(
                "/api/campaigns",
                json={"card_id": card_id, "lang": "zh", "campaign_id": "web-test"},
            )
            assert created.status_code == 200
            assert created.json()["campaign_id"] == "web-test"

            duplicate = await client.post(
                "/api/campaigns",
                json={"card_id": card_id, "lang": "zh", "campaign_id": "web-test"},
            )
            assert duplicate.status_code == 409

            campaigns = await client.get("/api/campaigns")
            assert any(c["campaign_id"] == "web-test" for c in campaigns.json()["campaigns"])

    asyncio.run(run())


def test_state_views_public_vs_gm(tmp_path):
    async def run():
        async with _make_client(tmp_path) as client:
            resources = (await client.get("/api/resources")).json()
            scenario_id = next(
                r["id"] for r in resources["scenarios"] if "station_zero" in r["id"]
            )
            await client.post(
                "/api/campaigns",
                json={"scenario_id": scenario_id, "lang": "en", "campaign_id": "web-state"},
            )
            public = await client.get("/api/campaigns/web-state/state")
            assert public.status_code == 200
            assert "saboteur" not in json.dumps(public.json())
            gm = await client.get("/api/campaigns/web-state/state?view=gm")
            assert "saboteur" in json.dumps(gm.json())
            assert gm.json()["story_framework"]["forbidden_revelations"]

    asyncio.run(run())


def test_turn_sse_fake_and_idempotent_request_id(tmp_path):
    async def run():
        async with _make_client(tmp_path) as client:
            resources = (await client.get("/api/resources")).json()
            scenario_id = next(
                r["id"] for r in resources["scenarios"] if "station_zero" in r["id"]
            )
            await client.post(
                "/api/campaigns",
                json={"scenario_id": scenario_id, "lang": "en", "campaign_id": "web-sse"},
            )

            async def play(request_id):
                events = []
                async with client.stream(
                    "POST",
                    "/api/campaigns/web-sse/turns",
                    json={
                        "player_input": "open the door carefully",
                        "request_id": request_id,
                        "fake": True,
                    },
                ) as resp:
                    assert resp.status_code == 200
                    current = None
                    async for line in resp.aiter_lines():
                        if line.startswith("event:"):
                            current = line[6:].strip()
                        elif line.startswith("data:"):
                            events.append((current, json.loads(line[5:])))
                return events

            first = await play("req-1")
            types = {event for event, _ in first}
            assert {"stage", "message", "turn_complete"} <= types
            assert any(event == "dice" for event, _ in first)
            complete = next(data for event, data in first if event == "turn_complete")
            assert complete["cached"] is False

            second = await play("req-1")
            complete2 = next(data for event, data in second if event == "turn_complete")
            assert complete2["cached"] is True

    asyncio.run(run())


def test_turn_json_mode_returns_full_reply(tmp_path):
    async def run():
        async with _make_client(tmp_path) as client:
            resources = (await client.get("/api/resources")).json()
            scenario_id = next(
                r["id"] for r in resources["scenarios"] if "station_zero" in r["id"]
            )
            await client.post(
                "/api/campaigns",
                json={"scenario_id": scenario_id, "lang": "en", "campaign_id": "web-json"},
            )
            body = {
                "player_input": "open the door carefully",
                "request_id": "json-1",
                "fake": True,
                "stream": False,
            }
            first = await client.post("/api/campaigns/web-json/turns", json=body)
            assert first.status_code == 200
            data = first.json()
            assert data["turn"] == 1
            assert data["cached"] is False
            assert data["gm_narration"]
            assert data["actor_text"]
            assert data["roll"] is not None
            assert data["gm_wrap_narration"] is None

            second = await client.post("/api/campaigns/web-json/turns", json=body)
            assert second.json()["cached"] is True

            state = (await client.get("/api/campaigns/web-json/state")).json()
            types = [item["type"] for item in state["transcript"]]
            assert "player" in types and "gm" in types and "actor" in types and "dice" in types
            assert any(
                item["type"] == "player" and "door" in item["text"]
                for item in state["transcript"]
            )

    asyncio.run(run())


def test_settings_roundtrip(tmp_path):
    async def run():
        async with _make_client(tmp_path) as client:
            current = await client.get("/api/settings")
            assert current.status_code == 200
            config = current.json()["config"]
            config["agents"]["gm"]["temperature"] = 0.77
            updated = await client.put("/api/settings", json=config)
            assert updated.status_code == 200
            assert updated.json()["config"]["agents"]["gm"]["temperature"] == 0.77
            again = (await client.get("/api/settings")).json()["config"]
            assert again["agents"]["gm"]["temperature"] == 0.77

    asyncio.run(run())


def test_unhandled_turn_errors_return_json_detail(tmp_path):
    async def run():
        class BoomSuite(FakeAgentSuite):
            async def gm_plan(self, view, player_input):
                raise RuntimeError("boom from GM")

        def factory(fake, locale):
            return BoomSuite(locale)

        async with _make_client(tmp_path, agent_factory=factory) as client:
            resources = (await client.get("/api/resources")).json()
            scenario_id = next(
                r["id"] for r in resources["scenarios"] if "station_zero" in r["id"]
            )
            await client.post(
                "/api/campaigns",
                json={"scenario_id": scenario_id, "lang": "en", "campaign_id": "web-err"},
            )
            res = await client.post(
                "/api/campaigns/web-err/turns",
                json={"player_input": "hello", "stream": False},
            )
            assert res.status_code == 500
            assert "boom from GM" in res.json()["detail"]

    asyncio.run(run())


def test_upload_endpoints(tmp_path):
    async def run():
        library = ResourceLibrary(upload_root=tmp_path / "uploads")
        async with _make_client(tmp_path, library=library) as client:
            world = {"name": "W", "entries": [{"id": 0, "content": "fact", "constant": True}]}
            ok = await client.post(
                "/api/resources/worlds",
                files={"file": ("w.json", json.dumps(world).encode(), "application/json")},
            )
            assert ok.status_code == 200
            assert ok.json()["kind"] == "worlds"

            bad = await client.post(
                "/api/resources/worlds",
                files={"file": ("bad.json", b"nope", "application/json")},
            )
            assert bad.status_code == 400

    asyncio.run(run())
