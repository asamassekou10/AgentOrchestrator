"""BandOrchestrator — the conductor that drives the tick clock and coordinates agents."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from virtual_band.events import EventBus, EventType, MusicalEvent
from virtual_band.agents import MusicianAgent

logger = logging.getLogger(__name__)


@dataclass
class SongStructure:
    """Describes a simple arrangement as a sequence of (section, chord, duration) tuples."""

    parts: list[tuple[str, str, int]] = field(default_factory=list)

    @staticmethod
    def default() -> "SongStructure":
        return SongStructure(parts=[
            # (section, chord, duration_in_ticks)
            ("intro", "Cmaj7", 8),
            ("verse", "Am7", 8),
            ("verse", "Fmaj7", 8),
            ("verse", "G", 8),
            ("chorus", "C", 8),
            ("chorus", "G", 8),
            ("chorus", "Am", 4),
            ("chorus", "F", 4),
            ("bridge", "Dm7", 8),
            ("bridge", "G", 8),
            ("outro", "Cmaj7", 8),
        ])

    def events_at(self, tick: int, source: str = "Orchestrator") -> list[MusicalEvent]:
        """Return chord/section change events if a boundary falls on this tick."""
        events: list[MusicalEvent] = []
        cursor = 0
        prev_section: str | None = None
        for section, chord, duration in self.parts:
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
                break
            cursor += duration
            prev_section = section
        return events

    @property
    def total_ticks(self) -> int:
        return sum(d for _, _, d in self.parts)


class BandOrchestrator:
    """Drives the musical timeline and coordinates all agents."""

    def __init__(
        self,
        bus: EventBus,
        agents: list[MusicianAgent],
        song: SongStructure | None = None,
    ) -> None:
        self.bus = bus
        self.agents = {a.name: a for a in agents}
        self.song = song or SongStructure.default()
        self._tick = 0

    async def perform(self) -> list[MusicalEvent]:
        """Run the full song and return the complete event history."""
        total = self.song.total_ticks
        logger.info("Performance starting — %d ticks", total)

        for tick in range(total):
            self._tick = tick

            # 1. Publish any structural events (chord/section changes).
            for event in self.song.events_at(tick):
                await self.bus.publish(event)

            # 2. Let every agent play this tick concurrently.
            coros = [agent.play_tick(tick) for agent in self.agents.values()]
            await asyncio.gather(*coros)

        logger.info("Performance complete — %d events produced", len(self.bus.history))
        return self.bus.history
