"""FastAPI backend — serves the UI and streams live performances via WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from virtual_band.events import EventBus, EventType, MusicalEvent
from virtual_band.agents import Drummer, Bassist, Pianist, Vocalist
from virtual_band.orchestrator import BandOrchestrator, SongStructure
from virtual_band.config import BandConfig
from virtual_band.songs import Song, SongStore
from virtual_band.llm_agent import create_llm_agents

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
config = BandConfig.from_env()

app = FastAPI(title="Virtual Band")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Persistent stores
song_store = SongStore(config.server.songs_dir)

# In-memory performance history store (last 20 performances)
_performances: dict[str, dict] = {}
MAX_PERFORMANCES = 20


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


# ── Pages ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# ── Song API ───────────────────────────────────────────────────────────

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


@app.get("/api/songs")
async def list_songs():
    return {"songs": song_store.list_songs()}


@app.get("/api/songs/{name}")
async def get_song(name: str):
    song = song_store.load(name)
    if not song:
        return {"error": "Song not found"}, 404
    return song.to_dict()


@app.post("/api/songs")
async def save_song(data: dict):
    song = Song.from_dict(data)
    errors = song.validate()
    if errors:
        return {"errors": errors}, 400
    path = song_store.save(song)
    return {"saved": True, "name": song.name}


@app.delete("/api/songs/{name}")
async def delete_song(name: str):
    deleted = song_store.delete(name)
    return {"deleted": deleted}


# ── Performance API ────────────────────────────────────────────────────

@app.get("/api/performances")
async def list_performances():
    return {
        "performances": [
            {"id": pid, "name": p.get("name", "Untitled"), "timestamp": p.get("timestamp", 0),
             "total_events": len(p.get("events", [])), "total_ticks": p.get("total_ticks", 0)}
            for pid, p in sorted(_performances.items(), key=lambda x: x[1].get("timestamp", 0), reverse=True)
        ]
    }


@app.get("/api/performances/{perf_id}")
async def get_performance(perf_id: str):
    perf = _performances.get(perf_id)
    if not perf:
        return {"error": "Performance not found"}, 404
    return perf


@app.get("/api/performances/{perf_id}/midi")
async def export_midi(perf_id: str):
    """Export a saved performance as a Standard MIDI file."""
    perf = _performances.get(perf_id)
    if not perf:
        return {"error": "Performance not found"}, 404

    midi_bytes = _events_to_midi(perf["events"], perf.get("tempo", 120))
    return StreamingResponse(
        BytesIO(midi_bytes),
        media_type="audio/midi",
        headers={"Content-Disposition": f"attachment; filename=virtual-band-{perf_id[:8]}.mid"}
    )


def _events_to_midi(events: list[dict], tempo: int = 120) -> bytes:
    """Convert event dicts to a Standard MIDI file bytes."""
    try:
        import mido
    except ImportError:
        # Fallback: return empty MIDI if mido not installed
        return b""

    mid = mido.MidiFile(ticks_per_beat=480)
    ticks_per_tick = 480  # one "tick" in our system = one quarter note subdivision

    # Create tracks per agent
    agent_tracks: dict[str, mido.MidiTrack] = {}
    agent_channels = {"Drummer": 9, "Bassist": 1, "Pianist": 2, "Vocalist": 3}

    for ev in events:
        if ev["type"] != "note_on" or ev.get("source") == "Orchestrator":
            continue
        source = ev["source"]
        if source not in agent_tracks:
            track = mido.MidiTrack()
            track.append(mido.MetaMessage("track_name", name=source, time=0))
            mid.tracks.append(track)
            agent_tracks[source] = track

    # Set tempo on first track
    if mid.tracks:
        mid.tracks[0].insert(0, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo), time=0))

    # Convert events to MIDI messages
    for source, track in agent_tracks.items():
        ch = agent_channels.get(source, 0)
        source_events = [e for e in events if e["source"] == source and e["type"] == "note_on" and e.get("pitch") is not None]
        source_events.sort(key=lambda e: e["tick"])

        last_tick = 0
        for ev in source_events:
            delta = (ev["tick"] - last_tick) * (ticks_per_tick // 4)
            pitch = max(0, min(127, ev["pitch"]))
            vel = max(1, min(127, ev.get("velocity", 100)))
            dur = max(1, ev.get("duration", 1)) * (ticks_per_tick // 4)

            track.append(mido.Message("note_on", note=pitch, velocity=vel, channel=ch, time=delta))
            track.append(mido.Message("note_off", note=pitch, velocity=0, channel=ch, time=dur))
            last_tick = ev["tick"]

    buf = BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


# ── Sync perform ───────────────────────────────────────────────────────

@app.get("/api/perform")
async def perform_sync():
    """Run a full performance synchronously and return the event history (for non-WS clients)."""
    bus = EventBus()
    agents = [Drummer(bus), Bassist(bus), Pianist(bus), Vocalist(bus)]
    song = SongStructure.default()
    orchestra = BandOrchestrator(bus, agents, song)
    history = await orchestra.perform()

    events = [_event_to_dict(e) for e in history]
    perf_id = _save_performance(events, song.total_ticks, 120)

    return {
        "performance_id": perf_id,
        "total_events": len(history),
        "events": events,
    }


# ── WebSocket perform ─────────────────────────────────────────────────

@app.websocket("/ws/perform")
async def perform_ws(ws: WebSocket):
    """Stream a live performance tick-by-tick over WebSocket."""
    await ws.accept()

    try:
        # Wait for the client to send a start message (optionally with custom song)
        raw = await ws.receive_text()
        cfg = json.loads(raw)

        # Build the song structure
        if "parts" in cfg:
            parts = [
                (p["section"], p["chord"], p["duration"], p["intensity"])
                for p in cfg["parts"]
            ]
            song = SongStructure(parts=parts)
        else:
            song = SongStructure.default()

        tempo = cfg.get("tempo", config.audio.default_tempo)
        use_llm = cfg.get("use_llm", False) and config.llm.enabled

        bus = EventBus()
        if use_llm:
            agents = create_llm_agents(bus, config.llm)
        else:
            agents = [Drummer(bus), Bassist(bus), Pianist(bus), Vocalist(bus)]
        orchestra = BandOrchestrator(bus, agents, song)

        agent_names = [a.name for a in agents]
        if use_llm:
            agent_names = ["Drummer", "Bassist", "Pianist", "Vocalist"]

        # Send song metadata
        await ws.send_json({
            "kind": "meta",
            "total_ticks": song.total_ticks,
            "tempo": tempo,
            "agents": agent_names,
            "llm_enabled": use_llm,
        })

        # Play tick-by-tick, streaming events to the client
        total = song.total_ticks
        history_offset = 0
        all_events: list[dict] = []

        for tick in range(total):
            orchestra._tick = tick

            # Structural events
            for event in song.events_at(tick):
                await bus.publish(event)

            # Agent play (with error isolation via orchestrator)
            coros = [orchestra._safe_play(agent, tick) for agent in orchestra.agents.values()]
            await asyncio.gather(*coros)

            # Send only new events since last tick
            history = bus.history
            new_events = history[history_offset:]
            history_offset = len(history)

            tick_events = [_event_to_dict(e) for e in new_events]
            all_events.extend(tick_events)

            await ws.send_json({
                "kind": "tick",
                "tick": tick,
                "events": tick_events,
            })

            # Pace to approximate real-time feel (adjustable by tempo)
            delay = 60.0 / tempo / 4  # 4 ticks per beat
            await asyncio.sleep(delay)

        # Save performance
        perf_id = _save_performance(all_events, song.total_ticks, tempo)

        # Final summary
        await ws.send_json({
            "kind": "done",
            "total_events": len(all_events),
            "performance_id": perf_id,
        })

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as exc:
        logger.exception("WebSocket error: %s", exc)
        try:
            await ws.send_json({"kind": "error", "message": str(exc)})
        except Exception:
            pass


def _save_performance(events: list[dict], total_ticks: int, tempo: int) -> str:
    """Save a performance to the in-memory store. Returns the performance ID."""
    perf_id = str(uuid.uuid4())[:12]
    _performances[perf_id] = {
        "id": perf_id,
        "name": f"Performance {len(_performances) + 1}",
        "timestamp": time.time(),
        "events": events,
        "total_ticks": total_ticks,
        "tempo": tempo,
    }
    # Trim old performances
    while len(_performances) > MAX_PERFORMANCES:
        oldest = min(_performances, key=lambda k: _performances[k]["timestamp"])
        del _performances[oldest]
    return perf_id


# ── Config info ────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    """Return public-safe configuration info."""
    return {
        "default_tempo": config.audio.default_tempo,
        "llm_available": bool(config.llm.api_key and config.llm.enabled),
        "llm_model": config.llm.model if config.llm.enabled else None,
    }
