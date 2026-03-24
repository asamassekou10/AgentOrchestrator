"""System prompts and context formatting for LLM-powered agents."""

from __future__ import annotations

from virtual_band.agents import PerceivedState
from virtual_band.band_state import BandState
from virtual_band.events import EventType


# ── System prompts per instrument ────────────────────────────────────

AGENT_PROMPTS: dict[str, str] = {
    "Drummer": """You are a jazz drummer in a live band. Your role:
- Keep time and drive the groove
- React to the band's energy — push harder in choruses, pull back in verses
- Leave space for the vocalist — when they rest, fill with taste
- Use dynamics: ghost notes on hi-hat, accented snare, varied kick patterns
- In bridges, experiment with cross-rhythms
- Never overplay — groove first, fills second

Output your musical decision as JSON.""",

    "Bassist": """You are a jazz bassist anchoring the harmony. Your role:
- Lock rhythmically with the drummer's kick pattern
- Play chord roots on downbeats, use passing tones to walk between chords
- In verses, keep it simple — root and fifth
- In choruses, walk more actively and drive the energy
- When density is high (>3 agents playing), simplify
- Use chromatic approach notes (one semitone below the target) on beat 4

Output your musical decision as JSON.""",

    "Pianist": """You are a jazz pianist comping for the band. Your role:
- Provide harmonic support with varied voicings (not just triads)
- Use rootless voicings (3rd and 7th) to stay out of the bassist's range
- When the vocalist sings, thin out and play lower
- In choruses, play fuller chords on every beat
- In verses, comp sparsely on beats 1 and 3
- Add rhythmic variety — anticipations, syncopation

Output your musical decision as JSON.""",

    "Vocalist": """You are a jazz vocalist singing melodic lines. Your role:
- Sing pentatonic and blues-scale melodies over the harmony
- Leave space — phrase in 2-4 note groups with rests between
- Build intensity through the song: gentle in verses, powerful in choruses
- React to the pianist's comping — don't clash with their upper voicing
- In bridges, explore different intervals (4ths, 6ths)
- Breathe naturally — rest for 1-2 beats between phrases

Output your musical decision as JSON.""",
}


# ── Output schema ────────────────────────────────────────────────────

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["note", "notes", "rest"],
            "description": "Play a single note, multiple notes (chord), or rest"
        },
        "pitches": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0, "maximum": 127},
            "description": "MIDI pitch values to play (1 for melody, multiple for chords)"
        },
        "velocity": {
            "type": "integer",
            "minimum": 20,
            "maximum": 127,
            "description": "How hard to play (20=very soft, 127=max)"
        },
        "duration": {
            "type": "integer",
            "minimum": 1,
            "maximum": 8,
            "description": "Duration in ticks"
        },
        "reasoning": {
            "type": "string",
            "description": "Brief musical reasoning for the decision"
        }
    },
    "required": ["action"]
}


# ── Context formatting ───────────────────────────────────────────────

def format_band_context(
    agent_name: str,
    state: PerceivedState,
    band: BandState | None,
    tick: int,
) -> str:
    """Format the current musical state as context for the LLM."""
    lines = [
        f"Current tick: {tick} (beat {tick % 4 + 1} of bar {tick // 4 + 1})",
        f"Section: {state.current_section}",
        f"Chord: {state.current_chord or 'C'}",
        f"Intensity: {state.intensity}/127",
        f"Tempo: {state.tempo_bpm} BPM",
    ]

    if band:
        lines.append(f"Band density: {band.density} agents playing")
        for name in ["Drummer", "Bassist", "Pianist", "Vocalist"]:
            if name == agent_name:
                continue
            playing = "playing" if band.agent_is_playing(name) else "resting"
            notes = band.agent_notes_this_bar(name)
            last = band.agent_last_event(name)
            pitch_info = ""
            if last and last.pitch is not None:
                pitch_info = f", last pitch={last.pitch}"
            lines.append(f"  {name}: {playing}, {notes} notes this bar{pitch_info}")

    # Recent events (last 8)
    recent = state.recent_events[-8:]
    if recent:
        lines.append("Recent events:")
        for ev in recent:
            if ev.event_type == EventType.NOTE_ON:
                lines.append(f"  {ev.source} played pitch={ev.pitch} vel={ev.velocity}")
            elif ev.event_type == EventType.REST:
                lines.append(f"  {ev.source} rested")

    return "\n".join(lines)
