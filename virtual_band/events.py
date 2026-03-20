"""Musical event system — the shared language agents use to communicate."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


class EventType(Enum):
    NOTE_ON = "note_on"
    NOTE_OFF = "note_off"
    CHORD_CHANGE = "chord_change"
    BEAT = "beat"
    TEMPO_CHANGE = "tempo_change"
    DYNAMIC_CHANGE = "dynamic_change"  # volume / intensity shift
    SECTION_CHANGE = "section_change"  # verse, chorus, bridge …
    REST = "rest"


@dataclass(frozen=True)
class MusicalEvent:
    """An immutable musical message passed between agents."""

    event_type: EventType
    source: str  # name of the agent that produced the event
    tick: int  # logical clock position (in ticks)
    pitch: int | None = None  # MIDI pitch 0-127
    velocity: int = 100  # MIDI-style velocity 0-127
    duration: int = 1  # length in ticks
    chord: str | None = None  # e.g. "Cmaj7", "Dm"
    section: str | None = None  # e.g. "verse", "chorus"
    meta: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        parts = [f"{self.event_type.value} from={self.source} tick={self.tick}"]
        if self.pitch is not None:
            parts.append(f"pitch={self.pitch}")
        if self.chord:
            parts.append(f"chord={self.chord}")
        if self.section:
            parts.append(f"section={self.section}")
        return f"MusicalEvent({', '.join(parts)})"


# Type alias for subscribers
Subscriber = Callable[[MusicalEvent], Awaitable[None]]


class EventBus:
    """Async pub/sub bus that lets agents broadcast and receive events."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscriber]] = {}
        self._history: list[MusicalEvent] = []

    def subscribe(self, agent_name: str, callback: Subscriber) -> None:
        self._subscribers.setdefault(agent_name, []).append(callback)

    async def publish(self, event: MusicalEvent) -> None:
        self._history.append(event)
        logger.debug("EventBus: %s", event)
        tasks = []
        for name, callbacks in self._subscribers.items():
            if name == event.source:
                continue  # don't echo back to the sender
            for cb in callbacks:
                tasks.append(asyncio.create_task(cb(event)))
        if tasks:
            await asyncio.gather(*tasks)

    @property
    def history(self) -> list[MusicalEvent]:
        return list(self._history)

    def clear(self) -> None:
        self._history.clear()
