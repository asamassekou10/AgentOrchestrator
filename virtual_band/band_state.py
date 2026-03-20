"""Shared read-only band state that all agents can inspect."""

from __future__ import annotations

from dataclasses import dataclass, field

from virtual_band.events import EventType, MusicalEvent


@dataclass
class AgentActivity:
    """Snapshot of one agent's recent activity."""

    last_event: MusicalEvent | None = None
    is_playing: bool = False
    notes_this_bar: int = 0


class BandState:
    """Observable state shared across all agents.

    Updated automatically by the EventBus after each publish.
    Agents hold a reference and can read (but should not write).
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentActivity] = {}
        self._density: int = 0  # how many agents played on the current tick
        self._current_tick: int = -1
        self._soloist: str | None = None
        self._events_this_tick: list[MusicalEvent] = []

    def update(self, event: MusicalEvent) -> None:
        """Called by EventBus on every publish."""
        # Reset per-tick counters when tick advances
        if event.tick > self._current_tick:
            self._current_tick = event.tick
            self._density = 0
            self._events_this_tick = []

        self._events_this_tick.append(event)

        # Track per-agent activity (skip orchestrator structural events)
        if event.source == "Orchestrator":
            return

        activity = self._agents.setdefault(event.source, AgentActivity())
        activity.last_event = event

        if event.event_type == EventType.NOTE_ON:
            activity.is_playing = True
            activity.notes_this_bar += 1
            self._density += 1
        elif event.event_type in (EventType.REST, EventType.NOTE_OFF):
            activity.is_playing = False

        # Reset bar counters every 4 ticks
        if event.tick % 4 == 0:
            for act in self._agents.values():
                act.notes_this_bar = 0

    @property
    def density(self) -> int:
        """Number of agents that played a note on the current tick."""
        return self._density

    @property
    def current_tick(self) -> int:
        return self._current_tick

    @property
    def soloist(self) -> str | None:
        return self._soloist

    @soloist.setter
    def soloist(self, name: str | None) -> None:
        self._soloist = name

    def agent_is_playing(self, name: str) -> bool:
        act = self._agents.get(name)
        return act.is_playing if act else False

    def agent_notes_this_bar(self, name: str) -> int:
        act = self._agents.get(name)
        return act.notes_this_bar if act else 0

    def agent_last_event(self, name: str) -> MusicalEvent | None:
        act = self._agents.get(name)
        return act.last_event if act else None
