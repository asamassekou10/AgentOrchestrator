"""Tests for the virtual band multi-agent system."""

import asyncio
import pytest

from virtual_band.events import EventBus, EventType, MusicalEvent
from virtual_band.band_state import BandState
from virtual_band.agents import (
    Drummer,
    Bassist,
    Pianist,
    Vocalist,
    PerceivedState,
    CHORD_ROOTS,
    KICK,
    SNARE,
    HI_HAT_CLOSED,
    HI_HAT_OPEN,
    RIDE,
    CRASH,
    CallAndResponse,
    LockIn,
    DropOut,
    swing_velocity,
    humanize_velocity,
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


@pytest.mark.asyncio
async def test_eventbus_updates_band_state():
    bus = EventBus()
    band_state = BandState()
    bus._band_state = band_state
    event = MusicalEvent(event_type=EventType.NOTE_ON, source="Drummer", tick=0, pitch=36)
    await bus.publish(event)
    assert band_state.density == 1
    assert band_state.agent_is_playing("Drummer")


# ── BandState ──────────────────────────────────────────────────────────


def test_band_state_tracks_density():
    bs = BandState()
    bs.update(MusicalEvent(event_type=EventType.NOTE_ON, source="A", tick=0, pitch=60))
    bs.update(MusicalEvent(event_type=EventType.NOTE_ON, source="B", tick=0, pitch=62))
    assert bs.density == 2


def test_band_state_resets_on_new_tick():
    bs = BandState()
    bs.update(MusicalEvent(event_type=EventType.NOTE_ON, source="A", tick=0, pitch=60))
    assert bs.density == 1
    bs.update(MusicalEvent(event_type=EventType.BEAT, source="Orchestrator", tick=1))
    assert bs.density == 0


def test_band_state_agent_activity():
    bs = BandState()
    assert not bs.agent_is_playing("X")
    bs.update(MusicalEvent(event_type=EventType.NOTE_ON, source="X", tick=0, pitch=60))
    assert bs.agent_is_playing("X")
    bs.update(MusicalEvent(event_type=EventType.REST, source="X", tick=1))
    assert not bs.agent_is_playing("X")


def test_band_state_notes_this_bar():
    bs = BandState()
    # All notes on the same tick so no bar-boundary reset
    for _ in range(3):
        bs.update(MusicalEvent(event_type=EventType.NOTE_ON, source="A", tick=1, pitch=60))
    assert bs.agent_notes_this_bar("A") == 3


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


def test_perceived_state_updates_intensity():
    state = PerceivedState()
    event = MusicalEvent(
        event_type=EventType.DYNAMIC_CHANGE, source="x", tick=0, velocity=110
    )
    state.update(event)
    assert state.intensity == 110


def test_perceived_state_caps_recent_events():
    state = PerceivedState()
    for i in range(100):
        state.update(
            MusicalEvent(event_type=EventType.BEAT, source="x", tick=i)
        )
    assert len(state.recent_events) == 64


# ── Swing & Humanize ──────────────────────────────────────────────────


def test_swing_velocity_accent_on_beats():
    """On-beats (0,2) should be louder than off-beats (1,3)."""
    on_beat = swing_velocity(0, 80, swing_amount=15)
    off_beat = swing_velocity(1, 80, swing_amount=15)
    assert on_beat > off_beat


def test_humanize_velocity_stays_in_range():
    for _ in range(100):
        v = humanize_velocity(100, amount=10)
        assert 20 <= v <= 127


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
    # Set to verse section which has kick on beats 0, 2
    drummer.state.current_section = "verse"
    events = await drummer.play_tick(0)
    pitches = {e.pitch for e in events}
    assert KICK in pitches


@pytest.mark.asyncio
async def test_drummer_snare_on_backbeats():
    bus = EventBus()
    drummer = Drummer(bus)
    drummer.state.current_section = "verse"
    events = await drummer.play_tick(1)
    pitches = {e.pitch for e in events}
    assert SNARE in pitches


@pytest.mark.asyncio
async def test_drummer_section_strategy_intro():
    """In intro, drummer plays sparse pattern — kick only on beat 0."""
    bus = EventBus()
    drummer = Drummer(bus)
    drummer.state.current_section = "intro"
    events = await drummer.play_tick(2)
    pitches = {e.pitch for e in events}
    # Intro has kick only on beat 0, so beat 2 should have no kick
    assert KICK not in pitches


@pytest.mark.asyncio
async def test_drummer_fill_on_dynamic_jump():
    """Drummer triggers a fill when intensity jumps above 100."""
    bus = EventBus()
    drummer = Drummer(bus)
    drummer.state.intensity = 70
    # Simulate a big dynamic change
    dynamic_event = MusicalEvent(
        event_type=EventType.DYNAMIC_CHANGE, source="Orchestrator", tick=0, velocity=110
    )
    await drummer._on_event(dynamic_event)
    assert drummer._fill_active is True


@pytest.mark.asyncio
async def test_drummer_uses_ride_in_chorus():
    """Drummer should use ride cymbal in chorus."""
    bus = EventBus()
    drummer = Drummer(bus)
    drummer.state.current_section = "chorus"
    events = await drummer.play_tick(0)
    pitches = {e.pitch for e in events}
    assert RIDE in pitches


@pytest.mark.asyncio
async def test_drummer_ghost_notes():
    """Drummer may produce ghost notes (low velocity snare) in verse/chorus."""
    bus = EventBus()
    drummer = Drummer(bus)
    drummer.state.current_section = "chorus"
    # Ghost notes happen on off-beats with some probability; run many ticks
    ghost_seen = False
    for tick in range(100):
        events = await drummer.play_tick(tick)
        for e in events:
            if e.pitch == SNARE and e.velocity < 50:
                ghost_seen = True
    # With 30% ghost prob over 100 ticks, we should see at least one
    assert ghost_seen


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


@pytest.mark.asyncio
async def test_bassist_locks_to_drummer_kick():
    """Bassist plays when drummer's kick is detected (lock-in)."""
    bus = EventBus()
    bassist = Bassist(bus)
    bassist.state.current_section = "intro"  # intro only plays beat 0
    # Simulate drummer kick on beat 2
    kick_event = MusicalEvent(
        event_type=EventType.NOTE_ON, source="Drummer", tick=2, pitch=KICK
    )
    await bassist._on_event(kick_event)
    events = await bassist.play_tick(2)
    assert len(events) >= 1  # should play even though intro pattern has only beat 0


@pytest.mark.asyncio
async def test_bassist_section_velocity():
    """Bassist adjusts velocity based on section strategy."""
    bus = EventBus()
    bassist = Bassist(bus)
    bassist.state.intensity = 80

    bassist.state.current_section = "intro"
    events_intro = await bassist.play_tick(0)

    bassist.state.current_section = "chorus"
    events_chorus = await bassist.play_tick(4)  # next downbeat

    if events_intro and events_chorus:
        # Chorus should generally be louder (vel_offset +5 vs -10)
        # Due to humanization, we just check chorus isn't quieter by a lot
        assert events_chorus[0].velocity >= events_intro[0].velocity - 20


# ── Pianist ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pianist_plays_chord_voicing_on_downbeat():
    bus = EventBus()
    pianist = Pianist(bus)
    pianist.state.current_section = "chorus"  # full voicing in chorus
    events = await pianist.play_tick(0)
    assert len(events) == 3  # default C triad


@pytest.mark.asyncio
async def test_pianist_rests_on_offbeats():
    bus = EventBus()
    pianist = Pianist(bus)
    pianist.state.current_section = "intro"
    events = await pianist.play_tick(1)
    assert len(events) == 0


@pytest.mark.asyncio
async def test_pianist_sparse_in_intro():
    """Intro uses sparse voicing — only 2 notes."""
    bus = EventBus()
    pianist = Pianist(bus)
    pianist.state.current_section = "intro"
    events = await pianist.play_tick(0)
    assert len(events) == 2


@pytest.mark.asyncio
async def test_pianist_avoids_vocal_register():
    """When vocalist is singing, pianist drops upper notes."""
    bus = EventBus()
    pianist = Pianist(bus)
    pianist.state.current_section = "chorus"
    pianist._voicing = (48, 52, 55, 59, 64)  # includes notes >= 60

    # Simulate vocalist singing
    vocal_event = MusicalEvent(
        event_type=EventType.NOTE_ON, source="Vocalist", tick=0, pitch=65
    )
    await pianist._on_event(vocal_event)

    events = await pianist.play_tick(0)
    for e in events:
        assert e.pitch < 60


@pytest.mark.asyncio
async def test_pianist_rootless_voicings_in_bridge():
    """Bridge uses rootless voicings."""
    bus = EventBus()
    pianist = Pianist(bus)
    pianist.state.current_section = "bridge"
    # Set chord to Cmaj7 which has rootless voicing (52, 59)
    chord_event = MusicalEvent(
        event_type=EventType.CHORD_CHANGE, source="Orchestrator", tick=0, chord="Cmaj7"
    )
    await pianist._on_event(chord_event)

    events = await pianist.play_tick(0)
    pitches = {e.pitch for e in events}
    # Rootless voicing should not contain the root (48=C3)
    assert 48 not in pitches


# ── Vocalist ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vocalist_produces_pentatonic_pitches():
    bus = EventBus()
    vocalist = Vocalist(bus)
    vocalist.state.current_section = "verse"
    pitches = set()
    for tick in range(50):
        vocalist._phrase_rest_counter = 0  # force no rest for testing
        events = await vocalist.play_tick(tick)
        for e in events:
            if e.event_type == EventType.NOTE_ON:
                pitches.add(e.pitch)
    # All pitches should be within the pentatonic set relative to root 60
    pentatonic = {60 + i for i in [0, 2, 4, 7, 9, 12]}
    assert pitches.issubset(pentatonic)


@pytest.mark.asyncio
async def test_vocalist_rests_in_intro():
    """Vocalist should not sing during intro."""
    bus = EventBus()
    vocalist = Vocalist(bus)
    vocalist.state.current_section = "intro"
    events = await vocalist.play_tick(0)
    assert len(events) == 0  # only REST emitted, no NOTE_ON returned


@pytest.mark.asyncio
async def test_vocalist_emits_rest_events():
    """Vocalist emits REST events when not singing, allowing other agents to react."""
    bus = EventBus()
    received = []

    async def on_event(e):
        received.append(e)

    bus.subscribe("listener", on_event)
    vocalist = Vocalist(bus)
    vocalist.state.current_section = "intro"  # inactive section
    await vocalist.play_tick(0)
    rest_events = [e for e in received if e.event_type == EventType.REST]
    assert len(rest_events) >= 1


@pytest.mark.asyncio
async def test_vocalist_uses_blues_scale_in_bridge():
    """Vocalist should use blues scale intervals in bridge section."""
    bus = EventBus()
    vocalist = Vocalist(bus)
    vocalist.state.current_section = "bridge"
    pitches = set()
    for tick in range(100):
        vocalist._phrase_rest_counter = 0
        events = await vocalist.play_tick(tick)
        for e in events:
            if e.event_type == EventType.NOTE_ON:
                pitches.add(e.pitch)
    # Blues scale includes b3 (3), b5 (6), b7 (10) relative to root 60
    blues_set = {60 + i for i in [0, 3, 5, 6, 7, 10, 12]}
    assert pitches.issubset(blues_set)


@pytest.mark.asyncio
async def test_vocalist_phrase_contour():
    """Vocalist should build phrases with some stepwise motion."""
    bus = EventBus()
    vocalist = Vocalist(bus)
    vocalist.state.current_section = "verse"
    last_pitch = None
    step_count = 0
    for tick in range(50):
        vocalist._phrase_rest_counter = 0
        events = await vocalist.play_tick(tick)
        for e in events:
            if e.event_type == EventType.NOTE_ON:
                if last_pitch is not None and abs(e.pitch - last_pitch) <= 4:
                    step_count += 1
                last_pitch = e.pitch
    # At least some stepwise motion should occur
    assert step_count > 5


# ── Interaction Patterns ───────────────────────────────────────────────


def test_call_and_response_alternates():
    cr = CallAndResponse("A", "B", phrase_length=2)
    bs = BandState()
    # Ticks 0,1 → caller plays; ticks 2,3 → responder plays
    assert cr.should_agent_play("A", 0, bs) is True
    assert cr.should_agent_play("B", 0, bs) is False
    assert cr.should_agent_play("A", 2, bs) is False
    assert cr.should_agent_play("B", 2, bs) is True
    # Non-participant → None
    assert cr.should_agent_play("C", 0, bs) is None


def test_dropout_features_soloist():
    do = DropOut(soloist="Vocalist", backing=["Pianist", "Bassist"])
    bs = BandState()
    # Soloist always plays
    assert do.should_agent_play("Vocalist", 1, bs) is True
    # Backing only on downbeats
    assert do.should_agent_play("Pianist", 0, bs) is True
    assert do.should_agent_play("Pianist", 1, bs) is False
    # Non-participant → None
    assert do.should_agent_play("Drummer", 1, bs) is None


# ── SongStructure ───────────────────────────────────────────────────────


def test_song_structure_total_ticks():
    song = SongStructure.default()
    assert song.total_ticks == sum(d for _, _, d, _ in song.parts)


def test_song_structure_events_at_boundaries():
    song = SongStructure.default()
    events = song.events_at(0)
    types = {e.event_type for e in events}
    assert EventType.SECTION_CHANGE in types
    assert EventType.CHORD_CHANGE in types
    assert EventType.DYNAMIC_CHANGE in types
    assert EventType.BEAT in types


def test_song_structure_beat_every_tick():
    song = SongStructure.default()
    for tick in range(song.total_ticks):
        events = song.events_at(tick)
        beat_events = [e for e in events if e.event_type == EventType.BEAT]
        assert len(beat_events) == 1


def test_song_structure_no_structural_events_mid_section():
    song = SongStructure.default()
    events = song.events_at(3)  # middle of first section
    # Should have BEAT but no chord/section/dynamic changes
    types = {e.event_type for e in events}
    assert EventType.CHORD_CHANGE not in types
    assert EventType.SECTION_CHANGE not in types
    assert EventType.BEAT in types


def test_song_structure_beat_metadata():
    song = SongStructure.default()
    events = song.events_at(5)
    beat_event = [e for e in events if e.event_type == EventType.BEAT][0]
    assert beat_event.meta["beat_in_bar"] == 1  # tick 5 % 4 == 1
    assert beat_event.meta["bar"] == 1  # tick 5 // 4 == 1


# ── Full orchestration ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_performance_produces_events():
    bus = EventBus()
    agents = [Drummer(bus), Bassist(bus), Pianist(bus), Vocalist(bus)]
    song = SongStructure(parts=[
        ("intro", "C", 4, 50),
        ("verse", "Am", 4, 80),
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
    song = SongStructure(parts=[("intro", "C", 4, 50)])
    orchestra = BandOrchestrator(bus, agents, song)
    history = await orchestra.perform()

    max_tick = max(e.tick for e in history)
    assert max_tick < song.total_ticks


@pytest.mark.asyncio
async def test_intensity_changes_across_sections():
    """Intensity should change when crossing from a quiet section to a loud one."""
    bus = EventBus()
    agents = [Drummer(bus)]
    song = SongStructure(parts=[
        ("intro", "C", 4, 40),
        ("chorus", "C", 4, 110),
    ])
    orchestra = BandOrchestrator(bus, agents, song)
    history = await orchestra.perform()

    dynamic_events = [e for e in history if e.event_type == EventType.DYNAMIC_CHANGE]
    assert len(dynamic_events) == 2
    assert dynamic_events[0].velocity == 40
    assert dynamic_events[1].velocity == 110


@pytest.mark.asyncio
async def test_band_state_wired_to_agents():
    """BandOrchestrator should give all agents a shared BandState."""
    bus = EventBus()
    agents = [Drummer(bus), Bassist(bus)]
    orchestra = BandOrchestrator(bus, agents)
    assert all(a.band is orchestra.band_state for a in orchestra.agents.values())


@pytest.mark.asyncio
async def test_drummer_varies_by_section():
    """Drummer's patterns should differ between intro and chorus."""
    bus = EventBus()
    drummer = Drummer(bus)
    song = SongStructure(parts=[
        ("intro", "C", 4, 50),
        ("chorus", "C", 4, 100),
    ])
    orchestra = BandOrchestrator(bus, [drummer], song)
    history = await orchestra.perform()

    intro_events = [e for e in history if e.tick < 4 and e.source == "Drummer" and e.event_type == EventType.NOTE_ON]
    chorus_events = [e for e in history if e.tick >= 4 and e.source == "Drummer" and e.event_type == EventType.NOTE_ON]

    # Chorus should have more drum hits (kick on every beat vs. only beat 0 in intro)
    assert len(chorus_events) > len(intro_events)


@pytest.mark.asyncio
async def test_orchestrator_error_isolation():
    """If an agent raises an exception, the orchestrator should continue."""
    bus = EventBus()
    drummer = Drummer(bus)
    bassist = Bassist(bus)

    # Monkey-patch bassist to raise
    original_play = bassist.play_tick

    async def broken_play(tick):
        if tick == 2:
            raise RuntimeError("test error")
        return await original_play(tick)

    bassist.play_tick = broken_play

    song = SongStructure(parts=[("verse", "C", 4, 80)])
    orchestra = BandOrchestrator(bus, [drummer, bassist], song)
    history = await orchestra.perform()

    # Drummer should still have produced events even though bassist crashed on tick 2
    drummer_events = [e for e in history if e.source == "Drummer" and e.event_type == EventType.NOTE_ON]
    assert len(drummer_events) >= 4


# ── PerformanceAnalyzer ────────────────────────────────────────────────


class PerformanceAnalyzer:
    """Compute metrics from a completed performance's event history."""

    def __init__(self, history: list[MusicalEvent]) -> None:
        self.history = history

    def density_per_tick(self) -> dict[int, int]:
        """Count of NOTE_ON events per tick."""
        counts: dict[int, int] = {}
        for e in self.history:
            if e.event_type == EventType.NOTE_ON:
                counts[e.tick] = counts.get(e.tick, 0) + 1
        return counts

    def agent_note_counts(self) -> dict[str, int]:
        """Total NOTE_ON events per agent."""
        counts: dict[str, int] = {}
        for e in self.history:
            if e.event_type == EventType.NOTE_ON and e.source != "Orchestrator":
                counts[e.source] = counts.get(e.source, 0) + 1
        return counts

    def rhythmic_alignment(self, agent_a: str, agent_b: str) -> float:
        """Fraction of ticks where both agents play simultaneously."""
        ticks_a = {e.tick for e in self.history if e.source == agent_a and e.event_type == EventType.NOTE_ON}
        ticks_b = {e.tick for e in self.history if e.source == agent_b and e.event_type == EventType.NOTE_ON}
        if not ticks_a or not ticks_b:
            return 0.0
        overlap = ticks_a & ticks_b
        union = ticks_a | ticks_b
        return len(overlap) / len(union)


@pytest.mark.asyncio
async def test_performance_analyzer_density():
    bus = EventBus()
    agents = [Drummer(bus), Bassist(bus)]
    song = SongStructure(parts=[("verse", "C", 4, 80)])
    orchestra = BandOrchestrator(bus, agents, song)
    history = await orchestra.perform()

    analyzer = PerformanceAnalyzer(history)
    density = analyzer.density_per_tick()
    # Tick 0 should have both drummer and bassist playing
    assert density.get(0, 0) >= 2


@pytest.mark.asyncio
async def test_performance_analyzer_agent_counts():
    bus = EventBus()
    agents = [Drummer(bus), Bassist(bus)]
    song = SongStructure(parts=[("verse", "C", 8, 80)])
    orchestra = BandOrchestrator(bus, agents, song)
    history = await orchestra.perform()

    analyzer = PerformanceAnalyzer(history)
    counts = analyzer.agent_note_counts()
    assert counts.get("Drummer", 0) > 0
    assert counts.get("Bassist", 0) > 0


@pytest.mark.asyncio
async def test_rhythmic_alignment_drummer_bassist():
    """Drummer and Bassist should have some rhythmic alignment."""
    bus = EventBus()
    agents = [Drummer(bus), Bassist(bus)]
    song = SongStructure(parts=[("verse", "C", 16, 80)])
    orchestra = BandOrchestrator(bus, agents, song)
    history = await orchestra.perform()

    analyzer = PerformanceAnalyzer(history)
    alignment = analyzer.rhythmic_alignment("Drummer", "Bassist")
    assert alignment > 0.0  # they should overlap on some beats


# ── Config ─────────────────────────────────────────────────────────────


def test_config_from_env():
    from virtual_band.config import BandConfig
    config = BandConfig.from_env()
    assert config.audio.default_tempo == 120
    assert config.server.port == 8000


# ── Song model ─────────────────────────────────────────────────────────


def test_song_validation():
    from virtual_band.songs import Song
    song = Song(name="Test", parts=[
        {"section": "intro", "chord": "C", "duration": 4, "intensity": 50},
        {"section": "verse", "chord": "Am", "duration": 8, "intensity": 80},
    ])
    assert song.validate() == []


def test_song_validation_errors():
    from virtual_band.songs import Song
    song = Song(name="", parts=[
        {"section": "invalid", "chord": "Xm9", "duration": -1, "intensity": 200},
    ])
    errors = song.validate()
    assert len(errors) >= 3  # name, section, chord, duration, intensity


def test_song_to_song_structure():
    from virtual_band.songs import Song
    song = Song(name="Test", parts=[
        {"section": "intro", "chord": "C", "duration": 4, "intensity": 50},
    ])
    ss = song.to_song_structure()
    assert ss.total_ticks == 4
    assert ss.parts[0] == ("intro", "C", 4, 50)


def test_song_roundtrip():
    from virtual_band.songs import Song
    original = Song(name="Test", parts=[
        {"section": "intro", "chord": "C", "duration": 4, "intensity": 50},
    ])
    data = original.to_dict()
    restored = Song.from_dict(data)
    assert restored.name == original.name
    assert restored.parts == original.parts
