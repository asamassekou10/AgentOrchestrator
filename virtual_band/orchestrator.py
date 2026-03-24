"""BandOrchestrator — the conductor that drives the tick clock and coordinates agents."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from virtual_band.events import EventBus, EventType, MusicalEvent
from virtual_band.agents import MusicianAgent, InteractionPattern
from virtual_band.band_state import BandState

logger = logging.getLogger(__name__)


@dataclass
class SongStructure:
    """Describes a simple arrangement as a sequence of (section, chord, duration) tuples."""

    # Each part: (section, chord, duration_in_ticks, target_intensity)
    parts: list[tuple[str, str, int, int]] = field(default_factory=list)

    @staticmethod
    def default() -> "SongStructure":
        return SongStructure(parts=[
            # (section, chord, duration_in_ticks, target_intensity)
            ("intro", "Cmaj7", 8, 50),
            ("verse", "Am7", 8, 70),
            ("verse", "Fmaj7", 8, 75),
            ("verse", "G", 8, 80),
            ("chorus", "C", 8, 100),
            ("chorus", "G", 8, 105),
            ("chorus", "Am", 4, 110),
            ("chorus", "F", 4, 100),
            ("bridge", "Dm7", 8, 65),
            ("bridge", "G", 8, 85),
            ("outro", "Cmaj7", 8, 45),
        ])

    def events_at(self, tick: int, source: str = "Orchestrator") -> list[MusicalEvent]:
        """Return structural events (section/chord/dynamic changes) at boundaries, plus BEAT every tick."""
        events: list[MusicalEvent] = []
        cursor = 0
        prev_section: str | None = None
        for section, chord, duration, intensity in self.parts:
            if tick == cursor:
                if section != prev_section:
                    events.append(MusicalEvent(
                        event_type=EventType.SECTION_CHANGE,
                        source=source,
                        tick=tick,
                        section=section,
                    ))
                events.append(MusicalEvent(
                    event_type=EventType.CHORD_CHANGE,
                    source=source,
                    tick=tick,
                    chord=chord,
                ))
                events.append(MusicalEvent(
                    event_type=EventType.DYNAMIC_CHANGE,
                    source=source,
                    tick=tick,
                    velocity=min(127, intensity),
                ))
                break
            cursor += duration
            prev_section = section

        # BEAT event every tick with beat position and bar info
        beat_in_bar = tick % 4
        bar_number = tick // 4
        events.append(MusicalEvent(
            event_type=EventType.BEAT,
            source=source,
            tick=tick,
            meta={"beat_in_bar": beat_in_bar, "bar": bar_number},
        ))

        return events

    @property
    def total_ticks(self) -> int:
        return sum(d for _, _, d, _ in self.parts)


class BandOrchestrator:
    """Drives the musical timeline and coordinates all agents."""

    def __init__(
        self,
        bus: EventBus,
        agents: list[MusicianAgent],
        song: SongStructure | None = None,
        interactions: list[InteractionPattern] | None = None,
    ) -> None:
        self.bus = bus
        self.band_state = BandState()
        self.bus._band_state = self.band_state
        self.agents = {a.name: a for a in agents}
        self.song = song or SongStructure.default()
        self.interactions = interactions or []
        self._tick = 0

        # Give every agent a reference to the shared band state
        for agent in self.agents.values():
            agent.band = self.band_state

    async def perform(self) -> list[MusicalEvent]:
        """Run the full song and return the complete event history."""
        total = self.song.total_ticks
        logger.info("Performance starting — %d ticks", total)

        for tick in range(total):
            self._tick = tick

            # 1. Publish any structural events (chord/section/dynamic changes + beat).
            for event in self.song.events_at(tick):
                await self.bus.publish(event)

            # 2. Let every agent play this tick concurrently, with error isolation.
            coros = [self._safe_play(agent, tick) for agent in self.agents.values()]
            await asyncio.gather(*coros)

        logger.info("Performance complete — %d events produced", len(self.bus.history))
        return self.bus.history

    async def _safe_play(self, agent: MusicianAgent, tick: int) -> list[MusicalEvent]:
        """Call agent.play_tick with error isolation — one agent crash doesn't stop the band."""
        try:
            return await agent.play_tick(tick)
        except Exception as exc:
            logger.error("Agent %s failed at tick %d: %s", agent.name, tick, exc)
            return []
