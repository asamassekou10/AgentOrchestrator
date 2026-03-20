#!/usr/bin/env python3
"""Run the virtual band and print a human-readable performance log."""

import asyncio
import logging

from virtual_band import (
    EventBus,
    Drummer,
    Bassist,
    Pianist,
    Vocalist,
    BandOrchestrator,
    LockIn,
    CallAndResponse,
)
from virtual_band.orchestrator import SongStructure


def print_performance(history):
    print(f"\n{'=' * 72}")
    print(f"  VIRTUAL BAND PERFORMANCE — {len(history)} events")
    print(f"{'=' * 72}\n")

    current_section = None
    for event in history:
        if event.section and event.section != current_section:
            current_section = event.section
            print(f"\n--- {current_section.upper()} ---")

        tag = f"[tick {event.tick:>3}] {event.source:<12}"
        detail = event.event_type.value

        if event.pitch is not None:
            detail += f"  pitch={event.pitch:<3}  vel={event.velocity}"
        if event.chord:
            detail += f"  chord={event.chord}"
        if event.duration > 1:
            detail += f"  dur={event.duration}"

        print(f"  {tag} {detail}")

    print(f"\n{'=' * 72}")
    print(f"  Done.")
    print(f"{'=' * 72}\n")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    bus = EventBus()

    agents = [
        Drummer(bus),
        Bassist(bus),
        Pianist(bus),
        Vocalist(bus),
    ]

    song = SongStructure.default()
    interactions = [
        LockIn(["Bassist", "Drummer"]),
        CallAndResponse("Vocalist", "Pianist", phrase_length=2),
    ]
    orchestra = BandOrchestrator(bus, agents, song, interactions=interactions)

    history = await orchestra.perform()
    print_performance(history)


if __name__ == "__main__":
    asyncio.run(main())
