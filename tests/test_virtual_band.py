"""Tests for the virtual band multi-agent system."""

import asyncio
import pytest

from virtual_band.events import EventBus, EventType, MusicalEvent
from virtual_band.agents import (
    Drummer,
    Bassist,
    Pianist,
    Vocalist,
    PerceivedState,
    CHORD_ROOTS,
)
from virtual_band.orchestrator import BandOrchestrator, SongStructure


# ── EventBus ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eventbus_delivers_to_subscribers():
    bus = EventBus()
    received = []

    async def on_event(e):
        received.append(e)

    bus.subscribe("listener", on_event)
    event = MusicalEvent(event_type=EventType.BEAT, source="other", tick=0)
    await bus.publish(event)

    assert len(received) == 1
    assert received[0] is event


@pytest.mark.asyncio
async def test_eventbus_does_not_echo_to_sender():
    bus = EventBus()
    received = []

    async def on_event(e):
        received.append(e)

    bus.subscribe("self", on_event)
    event = MusicalEvent(event_type=EventType.BEAT, source="self", tick=0)
    await bus.publish(event)

    assert len(received) == 0


@pytest.mark.asyncio
async def test_eventbus_history():
    bus = EventBus()
    e1 = MusicalEvent(event_type=EventType.BEAT, source="a", tick=0)
    e2 = MusicalEvent(event_type=EventType.BEAT, source="b", tick=1)
    await bus.publish(e1)
    await bus.publish(e2)

    assert bus.history == [e1, e2]
    bus.clear()
    assert bus.history == []


# ── PerceivedState ──────────────────────────────────────────────────────


def test_perceived_state_updates_chord():
    state = PerceivedState()
    event = MusicalEvent(
        event_type=EventType.CHORD_CHANGE, source="x", tick=4, chord="Am7"
    )
    state.update(event)
    assert state.current_chord == "Am7"
    assert state.current_tick == 4


def test_perceived_state_updates_section():
    state = PerceivedState()
    event = MusicalEvent(
        event_type=EventType.SECTION_CHANGE, source="x", tick=0, section="chorus"
    )
    state.update(event)
    assert state.current_section == "chorus"


def test_perceived_state_caps_recent_events():
    state = PerceivedState()
    for i in range(100):
        state.update(
            MusicalEvent(event_type=EventType.BEAT, source="x", tick=i)
        )
    assert len(state.recent_events) == 64


# ── Drummer ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drummer_plays_every_tick():
    bus = EventBus()
    drummer = Drummer(bus)
    for tick in range(8):
        events = await drummer.play_tick(tick)
        assert len(events) >= 1  # at least hi-hat


@pytest.mark.asyncio
async def test_drummer_kick_on_downbeats():
    bus = EventBus()
    drummer = Drummer(bus)
    events = await drummer.play_tick(0)
    pitches = {e.pitch for e in events}
    assert 36 in pitches  # KICK


@pytest.mark.asyncio
async def test_drummer_snare_on_backbeats():
    bus = EventBus()
    drummer = Drummer(bus)
    events = await drummer.play_tick(1)
    pitches = {e.pitch for e in events}
    assert 38 in pitches  # SNARE


# ── Bassist ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bassist_plays_root_on_downbeat():
    bus = EventBus()
    bassist = Bassist(bus)
    events = await bassist.play_tick(0)
    assert len(events) == 1
    assert events[0].pitch == 36  # default C root


@pytest.mark.asyncio
async def test_bassist_reacts_to_chord_change():
    bus = EventBus()
    bassist = Bassist(bus)
    chord_event = MusicalEvent(
        event_type=EventType.CHORD_CHANGE, source="Orchestrator", tick=0, chord="G"
    )
    await bassist._on_event(chord_event)
    events = await bassist.play_tick(4)  # next downbeat
    assert events[0].pitch == CHORD_ROOTS["G"]


# ── Pianist ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pianist_plays_chord_voicing_on_downbeat():
    bus = EventBus()
    pianist = Pianist(bus)
    events = await pianist.play_tick(0)
    assert len(events) == 3  # default C triad


@pytest.mark.asyncio
async def test_pianist_rests_on_offbeats():
    bus = EventBus()
    pianist = Pianist(bus)
    # intensity defaults to 80 > 60, so beat 3 may play
    # but beat 1 should always rest
    events = await pianist.play_tick(1)
    assert len(events) == 0


# ── Vocalist ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vocalist_produces_pentatonic_pitches():
    bus = EventBus()
    vocalist = Vocalist(bus)
    pitches = set()
    for tick in range(50):
        vocalist._phrase_rest_counter = 0  # force no rest for testing
        events = await vocalist.play_tick(tick)
        for e in events:
            pitches.add(e.pitch)
    # All pitches should be within the pentatonic set relative to root 60
    pentatonic = {60 + i for i in [0, 2, 4, 7, 9, 12]}
    assert pitches.issubset(pentatonic)


# ── SongStructure ───────────────────────────────────────────────────────


def test_song_structure_total_ticks():
    song = SongStructure.default()
    assert song.total_ticks == sum(d for _, _, d in song.parts)


def test_song_structure_events_at_boundaries():
    song = SongStructure.default()
    events = song.events_at(0)
    types = {e.event_type for e in events}
    assert EventType.SECTION_CHANGE in types
    assert EventType.CHORD_CHANGE in types


def test_song_structure_no_events_mid_section():
    song = SongStructure.default()
    events = song.events_at(3)  # middle of first section
    assert events == []


# ── Full orchestration ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_performance_produces_events():
    bus = EventBus()
    agents = [Drummer(bus), Bassist(bus), Pianist(bus), Vocalist(bus)]
    song = SongStructure(parts=[
        ("intro", "C", 4),
        ("verse", "Am", 4),
    ])
    orchestra = BandOrchestrator(bus, agents, song)
    history = await orchestra.perform()

    assert len(history) > 0
    sources = {e.source for e in history}
    assert "Drummer" in sources
    assert "Bassist" in sources
    assert "Pianist" in sources


@pytest.mark.asyncio
async def test_orchestrator_respects_song_length():
    bus = EventBus()
    agents = [Drummer(bus)]
    song = SongStructure(parts=[("intro", "C", 4)])
    orchestra = BandOrchestrator(bus, agents, song)
    history = await orchestra.perform()

    max_tick = max(e.tick for e in history)
    assert max_tick < song.total_ticks
