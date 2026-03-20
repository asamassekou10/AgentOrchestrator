"""FastAPI backend — serves the UI and streams live performances via WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from virtual_band.events import EventBus, EventType, MusicalEvent
from virtual_band.agents import Drummer, Bassist, Pianist, Vocalist
from virtual_band.orchestrator import BandOrchestrator, SongStructure

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Virtual Band")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _event_to_dict(event: MusicalEvent) -> dict:
    return {
        "type": event.event_type.value,
        "source": event.source,
        "tick": event.tick,
        "pitch": event.pitch,
        "velocity": event.velocity,
        "duration": event.duration,
        "chord": event.chord,
        "section": event.section,
        "meta": event.meta,
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/song")
async def get_default_song():
    song = SongStructure.default()
    return {
        "parts": [
            {"section": s, "chord": c, "duration": d, "intensity": i}
            for s, c, d, i in song.parts
        ],
        "total_ticks": song.total_ticks,
    }


@app.websocket("/ws/perform")
async def perform_ws(ws: WebSocket):
    """Stream a live performance tick-by-tick over WebSocket."""
    await ws.accept()

    try:
        # Wait for the client to send a start message (optionally with custom song)
        raw = await ws.receive_text()
        config = json.loads(raw)

        # Build the song structure
        if "parts" in config:
            parts = [
                (p["section"], p["chord"], p["duration"], p["intensity"])
                for p in config["parts"]
            ]
            song = SongStructure(parts=parts)
        else:
            song = SongStructure.default()

        tempo = config.get("tempo", 120)

        bus = EventBus()
        agents = [Drummer(bus), Bassist(bus), Pianist(bus), Vocalist(bus)]
        orchestra = BandOrchestrator(bus, agents, song)

        # Send song metadata
        await ws.send_json({
            "kind": "meta",
            "total_ticks": song.total_ticks,
            "tempo": tempo,
            "agents": [a.name for a in agents],
        })

        # Play tick-by-tick, streaming events to the client
        total = song.total_ticks
        history_offset = 0
        for tick in range(total):
            orchestra._tick = tick

            # Structural events
            for event in song.events_at(tick):
                await bus.publish(event)

            # Agent play
            coros = [agent.play_tick(tick) for agent in orchestra.agents.values()]
            await asyncio.gather(*coros)

            # Send only new events since last tick
            history = bus.history
            new_events = history[history_offset:]
            history_offset = len(history)

            await ws.send_json({
                "kind": "tick",
                "tick": tick,
                "events": [_event_to_dict(e) for e in new_events],
            })

            # Pace to approximate real-time feel (adjustable by tempo)
            delay = 60.0 / tempo / 4  # 4 ticks per beat
            await asyncio.sleep(delay)

        # Final summary
        history = bus.history
        await ws.send_json({
            "kind": "done",
            "total_events": len(history),
        })

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as exc:
        logger.exception("WebSocket error: %s", exc)
        try:
            await ws.send_json({"kind": "error", "message": str(exc)})
        except Exception:
            pass


@app.get("/api/perform")
async def perform_sync():
    """Run a full performance synchronously and return the event history (for non-WS clients)."""
    bus = EventBus()
    agents = [Drummer(bus), Bassist(bus), Pianist(bus), Vocalist(bus)]
    song = SongStructure.default()
    orchestra = BandOrchestrator(bus, agents, song)
    history = await orchestra.perform()
    return {
        "total_events": len(history),
        "events": [_event_to_dict(e) for e in history],
    }
