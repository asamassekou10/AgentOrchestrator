"""Musician agents — each one listens, decides, and plays independently."""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from virtual_band.events import EventBus, EventType, MusicalEvent
from virtual_band.band_state import BandState

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

    def __init__(self, name: str, bus: EventBus, band_state: BandState | None = None) -> None:
        self.name = name
        self.bus = bus
        self.state = PerceivedState()
        self.band: BandState | None = band_state
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


# ── Swing utility ──────────────────────────────────────────────────────

def swing_velocity(tick: int, base_vel: int, swing_amount: int = 15) -> int:
    """Apply swing feel: emphasize on-beats (0, 2), softer off-beats (1, 3)."""
    beat = tick % 4
    if beat in (0, 2):
        return min(127, base_vel + swing_amount)
    else:
        return max(20, base_vel - swing_amount)


def humanize_velocity(vel: int, amount: int = 8) -> int:
    """Add slight random variation to velocity for human feel."""
    return max(20, min(127, vel + random.randint(-amount, amount)))


# ── Concrete agents ────────────────────────────────────────────────────

# Pitch constants (MIDI) — expanded drum kit
KICK = 36
SNARE = 38
HI_HAT_CLOSED = 42
HI_HAT_OPEN = 46
RIDE = 51
CRASH = 49
GHOST_SNARE = 38  # same pitch, lower velocity


class Drummer(MusicianAgent):
    """Keeps time and drives the groove.

    Uses section-aware patterns with swing feel, ghost notes, and ride cymbal.
    """

    # Section strategy: hat style, kick pattern beats, snare pattern beats,
    #                   fill probability, use_ride, ghost_note_prob
    SECTION_STRATEGIES: dict[str, dict] = {
        "intro":  {"hat": "closed", "kick": (0,), "snare": (2,),
                   "fill_chance": 0.0, "use_ride": False, "ghost_prob": 0.0},
        "verse":  {"hat": "closed", "kick": (0, 2), "snare": (1, 3),
                   "fill_chance": 0.05, "use_ride": False, "ghost_prob": 0.2},
        "chorus": {"hat": "open_accent", "kick": (0, 1, 2, 3), "snare": (1, 3),
                   "fill_chance": 0.1, "use_ride": True, "ghost_prob": 0.3},
        "bridge": {"hat": "closed", "kick": (0, 2), "snare": (2,),
                   "fill_chance": 0.15, "use_ride": True, "ghost_prob": 0.15},
        "outro":  {"hat": "closed", "kick": (0,), "snare": (),
                   "fill_chance": 0.0, "use_ride": False, "ghost_prob": 0.0},
    }

    def __init__(self, bus: EventBus, name: str = "Drummer", band_state: BandState | None = None) -> None:
        super().__init__(name, bus, band_state)
        self._fill_active: bool = False
        self._prev_intensity: int = 80
        self._bar_count: int = 0

    def _strategy(self) -> dict:
        return self.SECTION_STRATEGIES.get(self.state.current_section, self.SECTION_STRATEGIES["verse"])

    async def react(self, event: MusicalEvent) -> None:
        # Play a fill when the vocalist signals a rest (space to fill)
        if event.event_type == EventType.REST and event.source == "Vocalist":
            strat = self._strategy()
            if random.random() < strat["fill_chance"] * 3:
                self._fill_active = True
        # React to dynamic changes with a fill on big intensity jumps
        if event.event_type == EventType.DYNAMIC_CHANGE:
            if event.velocity >= 100 and self._prev_intensity < 90:
                self._fill_active = True
            self._prev_intensity = event.velocity

    async def play_tick(self, tick: int) -> list[MusicalEvent]:
        events: list[MusicalEvent] = []
        beat_in_bar = tick % 4
        strat = self._strategy()

        if beat_in_bar == 0:
            self._bar_count += 1

        # Fill: rapid snare + open hi-hat burst, then resume normal
        if self._fill_active:
            self._fill_active = False
            for pitch in (SNARE, HI_HAT_OPEN):
                events.append(self._make_event(
                    tick=tick, event_type=EventType.NOTE_ON,
                    pitch=pitch, velocity=min(127, self.state.intensity + 15), duration=1,
                ))
            # Add crash on the downbeat after a fill
            if beat_in_bar == 0:
                events.append(self._make_event(
                    tick=tick, event_type=EventType.NOTE_ON,
                    pitch=CRASH, velocity=min(127, self.state.intensity + 20), duration=1,
                ))
            for e in events:
                await self.emit(e)
            return events

        # Hi-hat / ride selection with swing
        if strat["use_ride"]:
            hat_pitch = RIDE
        else:
            hat_pitch = HI_HAT_CLOSED
            if strat["hat"] == "open_accent" and beat_in_bar == 0:
                hat_pitch = HI_HAT_OPEN

        hat_vel = swing_velocity(tick, self._hat_velocity(tick), swing_amount=12)
        events.append(self._make_event(
            tick=tick, event_type=EventType.NOTE_ON,
            pitch=hat_pitch, velocity=hat_vel, duration=1,
        ))

        # Kick pattern from strategy with humanized velocity
        if beat_in_bar in strat["kick"]:
            kick_vel = humanize_velocity(min(127, self.state.intensity + 10))
            events.append(self._make_event(
                tick=tick, event_type=EventType.NOTE_ON,
                pitch=KICK, velocity=kick_vel, duration=1,
            ))

        # Snare pattern from strategy
        if beat_in_bar in strat["snare"]:
            snare_vel = swing_velocity(tick, self.state.intensity, swing_amount=8)
            events.append(self._make_event(
                tick=tick, event_type=EventType.NOTE_ON,
                pitch=SNARE, velocity=snare_vel, duration=1,
            ))
        # Ghost notes on off-beats (soft snare hits for groove)
        elif strat["ghost_prob"] > 0 and random.random() < strat["ghost_prob"]:
            ghost_vel = max(20, self.state.intensity // 3)
            events.append(self._make_event(
                tick=tick, event_type=EventType.NOTE_ON,
                pitch=GHOST_SNARE, velocity=ghost_vel, duration=1,
            ))

        # Crash cymbal on section transitions (first bar, beat 0)
        if beat_in_bar == 0 and self._bar_count == 1 and self.state.current_section in ("chorus", "bridge"):
            events.append(self._make_event(
                tick=tick, event_type=EventType.NOTE_ON,
                pitch=CRASH, velocity=min(127, self.state.intensity + 15), duration=1,
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

# Section strategies for bass: (play_beats, walk_enabled, velocity_offset)
BASS_STRATEGIES: dict[str, dict] = {
    "intro":  {"play_beats": (0,), "walk": False, "vel_offset": -10},
    "verse":  {"play_beats": (0, 2), "walk": False, "vel_offset": 0},
    "chorus": {"play_beats": (0, 1, 2, 3), "walk": True, "vel_offset": 5},
    "bridge": {"play_beats": (0, 2), "walk": False, "vel_offset": -5},
    "outro":  {"play_beats": (0, 2), "walk": False, "vel_offset": -15},
}


class Bassist(MusicianAgent):
    """Locks in with the drummer and follows the harmony.

    Now includes chromatic approach notes, walking bass variation,
    and swing-influenced velocity.
    """

    def __init__(self, bus: EventBus, name: str = "Bassist", band_state: BandState | None = None) -> None:
        super().__init__(name, bus, band_state)
        self._last_root: int = 36  # default C2
        self._next_root: int | None = None  # for approach notes
        self._drummer_kicked: bool = False  # whether drummer played kick this tick

    async def react(self, event: MusicalEvent) -> None:
        if event.event_type == EventType.CHORD_CHANGE and event.chord:
            new_root = CHORD_ROOTS.get(event.chord, self._last_root)
            self._next_root = new_root
            self._last_root = new_root
        # Lock-in: notice when drummer plays a kick
        if event.source == "Drummer" and event.event_type == EventType.NOTE_ON and event.pitch == KICK:
            self._drummer_kicked = True

    async def play_tick(self, tick: int) -> list[MusicalEvent]:
        events: list[MusicalEvent] = []
        beat_in_bar = tick % 4
        strat = BASS_STRATEGIES.get(self.state.current_section, BASS_STRATEGIES["verse"])

        # Determine if we should play this beat
        should_play = beat_in_bar in strat["play_beats"]

        # Lock-in bonus: also play if drummer kicked (even if not in our beat pattern)
        if self._drummer_kicked and not should_play and beat_in_bar not in (1,):
            should_play = True
        self._drummer_kicked = False

        if not should_play:
            return events

        # Pitch selection with walking bass and approach notes
        if beat_in_bar == 0:
            pitch = self._last_root
        elif beat_in_bar == 2:
            pitch = self._last_root + 7  # perfect fifth
        elif strat["walk"] and beat_in_bar == 3:
            # Chromatic approach note: one semitone below the next root
            if self._next_root and self.state.intensity > 60:
                pitch = self._next_root - 1  # chromatic approach from below
            elif self.state.intensity > 70:
                pitch = self._last_root + random.choice([3, 5, 7, 10])
            else:
                pitch = self._last_root + random.choice([5, 7])
        elif beat_in_bar == 1 and strat["play_beats"] == (0, 1, 2, 3):
            # Walking: third or fourth on beat 1
            pitch = self._last_root + random.choice([3, 4, 5])
        else:
            pitch = self._last_root

        # Density awareness: simplify if band is dense
        if self.band and self.band.density > 3 and beat_in_bar not in (0, 2):
            return events

        vel = max(30, min(127, self.state.intensity + strat["vel_offset"]))
        vel = swing_velocity(tick, vel, swing_amount=10)
        vel = humanize_velocity(vel, amount=5)
        events.append(self._make_event(
            tick=tick, event_type=EventType.NOTE_ON,
            pitch=pitch, velocity=vel, duration=1,
        ))

        for e in events:
            await self.emit(e)
        return events


# ── Piano / Keys ────────────────────────────────────────────────────────

# Extended voicings with 7ths and rootless voicings
CHORD_VOICINGS: dict[str, tuple[int, ...]] = {
    "C":     (48, 52, 55),       "Cm":    (48, 51, 55),
    "Cmaj7": (48, 52, 55, 59),   "Cm7":   (48, 51, 55, 58),
    "D":     (50, 54, 57),       "Dm":    (50, 53, 57),
    "Dmaj7": (50, 54, 57, 61),   "Dm7":   (50, 53, 57, 60),
    "E":     (52, 56, 59),       "Em":    (52, 55, 59),
    "Emaj7": (52, 56, 59, 63),   "Em7":   (52, 55, 59, 62),
    "F":     (53, 57, 60),       "Fm":    (53, 56, 60),
    "Fmaj7": (53, 57, 60, 64),   "Fm7":   (53, 56, 60, 63),
    "G":     (55, 59, 62),       "Gm":    (55, 58, 62),
    "Gmaj7": (55, 59, 62, 66),   "Gm7":   (55, 58, 62, 65),
    "A":     (57, 61, 64),       "Am":    (57, 60, 64),
    "Amaj7": (57, 61, 64, 68),   "Am7":   (57, 60, 64, 67),
    "B":     (59, 63, 66),       "Bm":    (59, 62, 66),
    "Bmaj7": (59, 63, 66, 70),   "Bm7":   (59, 62, 66, 69),
}

# Rootless voicings (3rd + 7th, used when bassist covers the root)
ROOTLESS_VOICINGS: dict[str, tuple[int, ...]] = {
    "C":     (52, 55),           "Cm":    (51, 55),
    "Cmaj7": (52, 59),           "Cm7":   (51, 58),
    "D":     (54, 57),           "Dm":    (53, 57),
    "Dmaj7": (54, 61),           "Dm7":   (53, 60),
    "E":     (56, 59),           "Em":    (55, 59),
    "F":     (57, 60),           "Fm":    (56, 60),
    "Fmaj7": (57, 64),           "Fm7":   (56, 63),
    "G":     (59, 62),           "Gm":    (58, 62),
    "Gmaj7": (59, 66),           "Gm7":   (58, 65),
    "A":     (61, 64),           "Am":    (60, 64),
    "Amaj7": (61, 68),           "Am7":   (60, 67),
    "B":     (63, 66),           "Bm":    (62, 66),
    "Bmaj7": (63, 70),           "Bm7":   (62, 69),
}

# Section strategies for piano
PIANO_STRATEGIES: dict[str, dict] = {
    "intro":  {"comp_beats": (0,), "voicing_style": "sparse", "vel_offset": -20, "rootless": False},
    "verse":  {"comp_beats": (0, 2), "voicing_style": "arpeggio", "vel_offset": -10, "rootless": True},
    "chorus": {"comp_beats": (0, 1, 2, 3), "voicing_style": "full", "vel_offset": 0, "rootless": False},
    "bridge": {"comp_beats": (0, 3), "voicing_style": "rootless", "vel_offset": -15, "rootless": True},
    "outro":  {"comp_beats": (0,), "voicing_style": "sparse", "vel_offset": -25, "rootless": False},
}


class Pianist(MusicianAgent):
    """Comps chords with extended voicings, rootless options, and rhythmic variation."""

    def __init__(self, bus: EventBus, name: str = "Pianist", band_state: BandState | None = None) -> None:
        super().__init__(name, bus, band_state)
        self._voicing: tuple[int, ...] = (48, 52, 55)
        self._rootless_voicing: tuple[int, ...] = (52, 55)
        self._vocalist_singing: bool = False
        self._current_chord: str = "C"

    async def react(self, event: MusicalEvent) -> None:
        if event.event_type == EventType.CHORD_CHANGE and event.chord:
            self._current_chord = event.chord
            self._voicing = CHORD_VOICINGS.get(event.chord, self._voicing)
            self._rootless_voicing = ROOTLESS_VOICINGS.get(event.chord, self._voicing[:2])
        # Track vocalist activity to stay out of the way
        if event.source == "Vocalist":
            if event.event_type == EventType.NOTE_ON:
                self._vocalist_singing = True
            elif event.event_type == EventType.REST:
                self._vocalist_singing = False

    async def play_tick(self, tick: int) -> list[MusicalEvent]:
        events: list[MusicalEvent] = []
        beat_in_bar = tick % 4
        strat = PIANO_STRATEGIES.get(self.state.current_section, PIANO_STRATEGIES["verse"])

        # Syncopation: occasionally play slightly ahead (anticipation)
        should_play = beat_in_bar in strat["comp_beats"]
        if not should_play:
            # Anticipation: 15% chance to play one beat early in chorus
            if self.state.current_section == "chorus" and beat_in_bar == 3 and random.random() < 0.15:
                should_play = True
            else:
                return events

        # Select voicing based on style
        style = strat["voicing_style"]
        if style == "rootless" or strat.get("rootless"):
            voicing = self._rootless_voicing
        else:
            voicing = self._voicing

        # Sparse: only bottom two notes
        if style == "sparse":
            voicing = voicing[:2]
        # Arpeggio: play one note per beat, cycling through the voicing
        elif style == "arpeggio":
            idx = beat_in_bar % len(voicing)
            voicing = (voicing[idx],)

        # When vocalist is singing, avoid upper voicing notes that clash
        if self._vocalist_singing and len(voicing) > 2:
            voicing = tuple(p for p in voicing if p < 60)
            if not voicing:
                voicing = self._voicing[:2]

        vel = max(40, min(127, self.state.intensity + strat["vel_offset"]))
        vel = swing_velocity(tick, vel, swing_amount=8)
        vel = humanize_velocity(vel, amount=5)

        for pitch in voicing:
            events.append(self._make_event(
                tick=tick, event_type=EventType.NOTE_ON,
                pitch=pitch, velocity=vel, duration=2,
            ))

        for e in events:
            await self.emit(e)
        return events


# ── Vocalist / Melodic lead ────────────────────────────────────────────

# Extended scale: pentatonic + blue note + chromatic passing tones
PENTATONIC_INTERVALS = [0, 2, 4, 7, 9, 12]
BLUES_INTERVALS = [0, 3, 5, 6, 7, 10, 12]  # blues scale

# Section strategies for vocalist
VOCAL_STRATEGIES: dict[str, dict] = {
    "intro":  {"rest_prob": 0.7, "duration_choices": (2, 3), "active": False, "scale": "pentatonic"},
    "verse":  {"rest_prob": 0.25, "duration_choices": (1, 2), "active": True, "scale": "pentatonic"},
    "chorus": {"rest_prob": 0.15, "duration_choices": (1, 1, 2), "active": True, "scale": "pentatonic"},
    "bridge": {"rest_prob": 0.4, "duration_choices": (2, 3), "active": True, "scale": "blues"},
    "outro":  {"rest_prob": 0.5, "duration_choices": (2, 3, 4), "active": True, "scale": "pentatonic"},
}


class Vocalist(MusicianAgent):
    """Sings melodic lines with phrase contour, blues scale in bridges, and dynamic breathing."""

    def __init__(self, bus: EventBus, name: str = "Vocalist", band_state: BandState | None = None) -> None:
        super().__init__(name, bus, band_state)
        self._root: int = 60  # C4
        self._phrase_rest_counter: int = 0  # ticks of silence remaining
        self._last_pitch: int | None = None  # for melodic contour
        self._phrase_direction: int = 1  # 1=ascending, -1=descending
        self._notes_in_phrase: int = 0

    async def react(self, event: MusicalEvent) -> None:
        if event.event_type == EventType.CHORD_CHANGE and event.chord:
            base = CHORD_ROOTS.get(event.chord, 36)
            self._root = base + 24  # transpose up two octaves for vocal range
        # When pianist is comping heavily, rest more to avoid clutter
        if self.band and event.source == "Pianist" and event.event_type == EventType.NOTE_ON:
            if self.band.agent_notes_this_bar("Pianist") > 6:
                self._phrase_rest_counter = max(self._phrase_rest_counter, 1)

    async def play_tick(self, tick: int) -> list[MusicalEvent]:
        events: list[MusicalEvent] = []
        strat = VOCAL_STRATEGIES.get(self.state.current_section, VOCAL_STRATEGIES["verse"])

        # Some sections the vocalist doesn't sing
        if not strat["active"]:
            # Emit REST so drummer can fill
            await self.emit(self._make_event(tick=tick, event_type=EventType.REST))
            return events

        # Natural phrasing: sing for a few beats, then rest
        if self._phrase_rest_counter > 0:
            self._phrase_rest_counter -= 1
            await self.emit(self._make_event(tick=tick, event_type=EventType.REST))
            return events

        # Probability of resting (section-dependent)
        if random.random() < strat["rest_prob"]:
            if random.random() < 0.3:
                self._phrase_rest_counter = random.randint(1, 3)
                # Reset phrase on longer rests
                self._notes_in_phrase = 0
                self._phrase_direction = random.choice([1, -1])
            await self.emit(self._make_event(tick=tick, event_type=EventType.REST))
            return events

        # Select scale based on section
        scale = BLUES_INTERVALS if strat.get("scale") == "blues" else PENTATONIC_INTERVALS

        # Melodic contour: prefer stepwise motion within phrases
        if self._last_pitch is not None and self._notes_in_phrase > 0:
            # Try to move by step in the current phrase direction
            last_interval = self._last_pitch - self._root
            current_idx = min(range(len(scale)), key=lambda i: abs(scale[i] - last_interval))
            next_idx = current_idx + self._phrase_direction

            if 0 <= next_idx < len(scale):
                interval = scale[next_idx]
            else:
                # Reverse direction at scale boundaries
                self._phrase_direction *= -1
                next_idx = current_idx + self._phrase_direction
                next_idx = max(0, min(len(scale) - 1, next_idx))
                interval = scale[next_idx]

            # Occasional leap (20% chance) for melodic interest
            if random.random() < 0.2:
                interval = random.choice(scale)
        else:
            interval = random.choice(scale)

        pitch = self._root + interval
        self._last_pitch = pitch
        self._notes_in_phrase += 1

        # End phrase after 3-5 notes
        if self._notes_in_phrase >= random.randint(3, 5):
            self._phrase_rest_counter = random.randint(1, 2)

        # Crescendo within phrases
        phrase_vel = self.state.intensity + (self._notes_in_phrase * 3)
        vel = min(127, humanize_velocity(phrase_vel, amount=5))

        events.append(self._make_event(
            tick=tick,
            event_type=EventType.NOTE_ON,
            pitch=pitch,
            velocity=vel,
            duration=random.choice(strat["duration_choices"]),
        ))

        for e in events:
            await self.emit(e)
        return events


# ── Interaction Patterns ───────────────────────────────────────────────

class InteractionPattern(ABC):
    """Base class for structured agent-agent interaction."""

    def __init__(self, agents: list[str]) -> None:
        self.agents = agents

    @abstractmethod
    def should_agent_play(self, agent_name: str, tick: int, band: BandState) -> bool | None:
        """Return True/False to override, or None to let the agent decide."""


class LockIn(InteractionPattern):
    """Bassist locks rhythmically to Drummer's kick pattern."""

    def should_agent_play(self, agent_name: str, tick: int, band: BandState) -> bool | None:
        if agent_name not in self.agents:
            return None
        # The bassist should play when the drummer's last event was a kick
        drummer_event = band.agent_last_event("Drummer")
        if drummer_event and drummer_event.tick == tick and drummer_event.pitch == KICK:
            return True
        return None


class DropOut(InteractionPattern):
    """Non-soloist agents reduce their activity to feature the soloist."""

    def __init__(self, soloist: str, backing: list[str]) -> None:
        super().__init__([soloist] + backing)
        self._soloist = soloist

    def should_agent_play(self, agent_name: str, tick: int, band: BandState) -> bool | None:
        if agent_name == self._soloist:
            return True
        if agent_name in self.agents:
            # Backing agents only play on downbeats
            return tick % 4 == 0
        return None


class CallAndResponse(InteractionPattern):
    """Two agents alternate: one plays a phrase, then rests while the other responds."""

    def __init__(self, caller: str, responder: str, phrase_length: int = 2) -> None:
        super().__init__([caller, responder])
        self._caller = caller
        self._responder = responder
        self._phrase_length = phrase_length

    def should_agent_play(self, agent_name: str, tick: int, band: BandState) -> bool | None:
        if agent_name not in self.agents:
            return None
        # Alternate in phrase_length-tick blocks
        block = (tick // self._phrase_length) % 2
        if agent_name == self._caller:
            return block == 0
        else:
            return block == 1
