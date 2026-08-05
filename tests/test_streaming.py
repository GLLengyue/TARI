import asyncio

from trpg_runtime.agents import FakeAgentSuite, TokenEmitter, prefix_delta
from trpg_runtime.projections import build_actor_view, build_gm_view
from trpg_runtime.rules import SpotlightGrant, SpotlightManager, SpotlightOwner
from trpg_runtime.scenario import load_scenario


def test_prefix_delta_appends_suffix():
    assert prefix_delta("ab", "abc") == ("c", "abc")
    assert prefix_delta("", "xy") == ("xy", "xy")


def test_prefix_delta_resets_on_rewrite():
    delta, prev = prefix_delta("abcdef", "xyz")
    assert delta == "xyz"
    assert prev == "xyz"


def test_token_emitter_tracks_channels():
    events = []
    emitter = TokenEmitter(lambda channel, delta: events.append((channel, delta)))
    emitter.emit("gm_narration", "Hello")
    emitter.emit("gm_narration", "Hello world")
    emitter.emit("gm_narration", "Hello world!")
    assert events == [("gm_narration", "Hello"), ("gm_narration", " world"), ("gm_narration", "!")]


def test_fake_suite_streams_full_text_then_returns_typed_output():
    async def run():
        state = load_scenario("examples/station_zero.yaml")
        suite = FakeAgentSuite("en")
        view = build_gm_view(state, [])
        events = []
        decision = await suite.gm_plan_stream(
            view, "open the door", lambda c, d: events.append((c, d))
        )
        assert decision.check_request is None or decision.next_spotlight is not None
        assert any(channel == "gm_reasoning" for channel, _ in events)

        state.spotlight = SpotlightManager.grant(
            SpotlightGrant(
                owner_type=SpotlightOwner.ACTOR,
                owner_id="mira",
                scopes={"own_action"},
                reason="test",
            ),
            1,
        )
        actor_view = build_actor_view(state, "mira", [], [])
        actor_events = []
        perf = await suite.actor_turn_stream(
            actor_view, lambda c, d: actor_events.append((c, d))
        )
        assert perf.speech
        assert any(channel == "actor_speech" for channel, _ in actor_events)

    asyncio.run(run())
