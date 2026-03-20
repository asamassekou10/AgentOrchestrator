"""Musician agents — each one listens, decides, and plays independently."""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from virtual_band.events import EventBus, EventType, MusicalEvent

logger = logging.getLogger(__name__)


# ── Shared musical state each agent maintains ──────────────────────────

@dataclass
class PerceivedState:
    """A lightweight snapshot of what an agent currently perceives."""

    current_tick: int = 0
    tempo_bpm: int = 120
    current_chord: str | None = None
    current_section: str = "intro"
    intensity: int = 80  # 0-127
    recent_events: list[MusicalEvent] = field(default_factory=list)

    def update(self, event: MusicalEvent) -> None:
        self.current_tick = max(self.current_tick, event.tick)
        if event.event_type == EventType.CHORD_CHANGE and event.chord:
            self.current_chord = event.chord
        if event.event_type == EventType.SECTION_CHANGE and event.section:
            self.current_section = event.section
        if event.event_type == EventType.DYNAMIC_CHANGE:
            self.intensity = event.velocity
        if event.event_type == EventType.TEMPO_CHANGE:
            self.tempo_bpm = event.velocity  # repurpose velocity for bpm
        self.recent_events.append(event)
        # Keep a sliding window so memory doesn't grow without bound.
        if len(self.recent_events) > 64:
            self.recent_events = self.recent_events[-64:]


# ── Base class ──────────────────────────────────────────────────────────

class MusicianAgent(ABC):
    """Abstract base for every musician in the band."""

    def __init__(self, name: str, bus: EventBus) -> None:
        self.name = name
        self.bus = bus
        self.state = PerceivedState()
        self._output_buffer: list[MusicalEvent] = []
        bus.subscribe(name, self._on_event)

    async def _on_event(self, event: MusicalEvent) -> None:
        """Callback wired to the EventBus."""
        self.state.update(event)
        await self.react(event)

    @abstractmethod
    async def react(self, event: MusicalEvent) -> None:
        """Decide how to respond to an incoming event."""

    @abstractmethod
    async def play_tick(self, tick: int) -> list[MusicalEvent]:
        """Generate events for the given tick (called by the orchestrator)."""

    async def emit(self, event: MusicalEvent) -> None:
        self._output_buffer.append(event)
        await self.bus.publish(event)

    def _make_event(self, tick: int, **kwargs) -> MusicalEvent:
        return MusicalEvent(source=self.name, tick=tick, **kwargs)


# ── Concrete agents ────────────────────────────────────────────────────

# Pitch constants (MIDI)
KICK = 36
SNARE = 38
HI_HAT_CLOSED = 42
HI_HAT_OPEN = 46


class Drummer(MusicianAgent):
    """Keeps time and drives the groove.

    Uses a simple pattern that varies with the current section/intensity.
    """

    def __init__(self, bus: EventBus, name: str = "Drummer") -> None:
        super().__init__(name, bus)

    async def react(self, event: MusicalEvent) -> None:
        # Drummer adjusts intensity when other agents signal dynamic changes.
        pass  # state is already updated by _on_event

    async def play_tick(self, tick: int) -> list[MusicalEvent]:
        events: list[MusicalEvent] = []
        beat_in_bar = tick % 4  # assume 4/4

        # Hi-hat on every tick
        events.append(self._make_event(
            tick=tick,
            event_type=EventType.NOTE_ON,
            pitch=HI_HAT_CLOSED,
            velocity=self._hat_velocity(tick),
            duration=1,
        ))

        # Kick on beats 0 and 2
        if beat_in_bar in (0, 2):
            events.append(self._make_event(
                tick=tick,
                event_type=EventType.NOTE_ON,
                pitch=KICK,
                velocity=min(127, self.state.intensity + 10),
                duration=1,
            ))

        # Snare on beats 1 and 3
        if beat_in_bar in (1, 3):
            events.append(self._make_event(
                tick=tick,
                event_type=EventType.NOTE_ON,
                pitch=SNARE,
                velocity=self.state.intensity,
                duration=1,
            ))

        for e in events:
            await self.emit(e)
        return events

    def _hat_velocity(self, tick: int) -> int:
        accent = 100 if tick % 2 == 0 else 70
        return min(127, accent + random.randint(-5, 5))


# ── Bass ────────────────────────────────────────────────────────────────

# Simple chord-root mapping (MIDI note numbers, octave 2)
CHORD_ROOTS: dict[str, int] = {
    "C": 36, "Cm": 36, "Cmaj7": 36, "Cm7": 36,
    "D": 38, "Dm": 38, "Dmaj7": 38, "Dm7": 38,
    "E": 40, "Em": 40, "Emaj7": 40, "Em7": 40,
    "F": 41, "Fm": 41, "Fmaj7": 41, "Fm7": 41,
    "G": 43, "Gm": 43, "Gmaj7": 43, "Gm7": 43,
    "A": 45, "Am": 45, "Amaj7": 45, "Am7": 45,
    "B": 47, "Bm": 47, "Bmaj7": 47, "Bm7": 47,
}


class Bassist(MusicianAgent):
    """Locks in with the drummer and follows the harmony."""

    def __init__(self, bus: EventBus, name: str = "Bassist") -> None:
        super().__init__(name, bus)
        self._last_root: int = 36  # default C2

    async def react(self, event: MusicalEvent) -> None:
        if event.event_type == EventType.CHORD_CHANGE and event.chord:
            self._last_root = CHORD_ROOTS.get(event.chord, self._last_root)

    async def play_tick(self, tick: int) -> list[MusicalEvent]:
        events: list[MusicalEvent] = []
        beat_in_bar = tick % 4

        # Root note on the downbeat, fifth on beat 2, walkup on beat 3
        if beat_in_bar == 0:
            pitch = self._last_root
        elif beat_in_bar == 2:
            pitch = self._last_root + 7  # perfect fifth
        elif beat_in_bar == 3 and self.state.intensity > 80:
            # chromatic walk-up to next root when intensity is high
            pitch = self._last_root + random.choice([5, 7, 10])
        else:
            return events  # rest on other beats

        events.append(self._make_event(
            tick=tick,
            event_type=EventType.NOTE_ON,
            pitch=pitch,
            velocity=self.state.intensity,
            duration=1,
        ))

        for e in events:
            await self.emit(e)
        return events


# ── Piano / Keys ────────────────────────────────────────────────────────

CHORD_VOICINGS: dict[str, tuple[int, ...]] = {
    "C": (48, 52, 55), "Cm": (48, 51, 55),
    "Cmaj7": (48, 52, 55, 59), "Cm7": (48, 51, 55, 58),
    "D": (50, 54, 57), "Dm": (50, 53, 57),
    "Dmaj7": (50, 54, 57, 61), "Dm7": (50, 53, 57, 60),
    "E": (52, 56, 59), "Em": (52, 55, 59),
    "F": (53, 57, 60), "Fm": (53, 56, 60),
    "G": (55, 59, 62), "Gm": (55, 58, 62),
    "A": (57, 61, 64), "Am": (57, 60, 64),
    "B": (59, 63, 66), "Bm": (59, 62, 66),
}


class Pianist(MusicianAgent):
    """Comps chords and reacts to the rhythm section."""

    def __init__(self, bus: EventBus, name: str = "Pianist") -> None:
        super().__init__(name, bus)
        self._voicing: tuple[int, ...] = (48, 52, 55)

    async def react(self, event: MusicalEvent) -> None:
        if event.event_type == EventType.CHORD_CHANGE and event.chord:
            self._voicing = CHORD_VOICINGS.get(event.chord, self._voicing)

    async def play_tick(self, tick: int) -> list[MusicalEvent]:
        events: list[MusicalEvent] = []
        beat_in_bar = tick % 4

        # Comp pattern: hit on beat 0, anticipation on the "and" of beat 2
        should_play = beat_in_bar == 0 or (beat_in_bar == 3 and self.state.intensity > 60)
        if not should_play:
            return events

        for pitch in self._voicing:
            events.append(self._make_event(
                tick=tick,
                event_type=EventType.NOTE_ON,
                pitch=pitch,
                velocity=max(40, self.state.intensity - 15),
                duration=2,
            ))

        for e in events:
            await self.emit(e)
        return events


# ── Vocalist / Melodic lead ────────────────────────────────────────────

# Pentatonic fragments relative to chord root for simple melodic ideas
PENTATONIC_INTERVALS = [0, 2, 4, 7, 9, 12]


class Vocalist(MusicianAgent):
    """Sings melodic lines over the harmony, leaving space dynamically."""

    def __init__(self, bus: EventBus, name: str = "Vocalist") -> None:
        super().__init__(name, bus)
        self._root: int = 60  # C4
        self._phrase_rest_counter: int = 0  # ticks of silence remaining

    async def react(self, event: MusicalEvent) -> None:
        if event.event_type == EventType.CHORD_CHANGE and event.chord:
            base = CHORD_ROOTS.get(event.chord, 36)
            self._root = base + 24  # transpose up two octaves for vocal range

    async def play_tick(self, tick: int) -> list[MusicalEvent]:
        events: list[MusicalEvent] = []

        # Natural phrasing: sing for a few beats, then rest
        if self._phrase_rest_counter > 0:
            self._phrase_rest_counter -= 1
            return events

        # 30 % chance of resting any given tick — creates breathing room
        if random.random() < 0.30:
            if random.random() < 0.3:
                self._phrase_rest_counter = random.randint(1, 3)
            return events

        interval = random.choice(PENTATONIC_INTERVALS)
        pitch = self._root + interval

        events.append(self._make_event(
            tick=tick,
            event_type=EventType.NOTE_ON,
            pitch=pitch,
            velocity=self.state.intensity,
            duration=random.choice([1, 2]),
        ))

        for e in events:
            await self.emit(e)
        return events
