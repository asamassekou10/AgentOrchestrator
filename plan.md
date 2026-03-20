# Virtual Band — Next-Level Implementation Plan

## Overview

Six major features to transform the Virtual Band from a visualization demo into a fully interactive, AI-powered, collaborative music creation platform.

---

## Phase 1: Audio Output (Web Audio API)

**Goal**: Hear the music in real time — every NOTE_ON becomes an audible sound.

### 1.1 Create `web/static/audio.js` — Audio Engine

- Initialize `AudioContext` on first user gesture (browser requirement)
- Build instrument synthesizers per agent:
  - **Drummer**: Sample-based (kick, snare, hi-hat) using short noise bursts + filtered oscillators
  - **Bassist**: Sawtooth oscillator → low-pass filter → gain envelope (attack 5ms, decay 100ms, sustain 0.6, release 200ms)
  - **Pianist**: Triangle + sine oscillators detuned slightly for warmth → filter → ADSR envelope
  - **Vocalist**: Sine oscillator with vibrato (LFO on frequency) → gentle reverb via ConvolverNode
- Expose `playNote(agentName, pitch, velocity, durationTicks, tempo)`:
  - Convert MIDI pitch → frequency: `440 * 2^((pitch-69)/12)`
  - Convert velocity (0-127) → gain (0.0-1.0)
  - Convert tick duration → seconds using tempo
  - Schedule note using `AudioContext.currentTime`
- Add master gain node + per-agent gain nodes (for volume mixing)
- Add simple reverb (ConvolverNode with generated impulse response)

### 1.2 Integrate into `app.js`

- On each WebSocket `tick` message, for every `NOTE_ON` event call `playNote()`
- Add volume sliders per agent in the agent panels (left sidebar)
- Add master volume + mute toggle in the top bar
- Ensure `AudioContext` is resumed on Play button click

### 1.3 Files Changed

| File | Change |
|---|---|
| `web/static/audio.js` | **New** — Audio engine |
| `web/static/app.js` | Import audio engine, call `playNote()` on tick events, add volume controls |
| `web/static/index.html` | Add `<script>` for audio.js, volume slider elements |
| `web/static/style.css` | Style volume sliders |

---

## Phase 2: LLM-Powered Agent Decision-Making

**Goal**: Replace rule-based logic with Claude API calls so agents genuinely improvise.

### 2.1 Create `virtual_band/llm_agent.py` — LLM-Powered Base Agent

- New class `LLMAgent(MusicianAgent)` that overrides `play_tick()` and `react()`
- Each agent gets a **system prompt** defining its musical personality:
  - Drummer: "You are a jazz drummer. You keep time but leave space. You respond to the bassist's groove..."
  - Bassist: "You lock with the drums. In verses you walk, in choruses you drive..."
  - etc.
- On `play_tick()`:
  - Build context message from `PerceivedState` + `BandState` (current chord, section, intensity, what other agents played recently, density)
  - Call Claude API with structured output: `{action: "note"|"rest", pitch?: int, velocity?: int, duration?: int}`
  - Parse response → emit corresponding event
- **Rate limiting**: Cache/batch — call LLM every N ticks (e.g., every 2-4 ticks) and interpolate between decisions to avoid excessive API calls
- **Fallback**: If LLM call fails or times out, fall back to existing rule-based logic

### 2.2 Create `virtual_band/prompts.py` — Agent Personality Prompts

- System prompts per instrument with musical vocabulary
- Context formatting functions: `format_band_context(perceived_state, band_state) -> str`
- Output schema definitions for structured responses

### 2.3 Update `orchestrator.py`

- Add `use_llm: bool` parameter to `BandOrchestrator`
- When enabled, instantiate `LLMAgent` variants instead of rule-based agents
- Expose LLM toggle via API

### 2.4 Update `web/app.py`

- Accept `use_llm` parameter in WebSocket handshake and `/api/perform`
- Pass API key from environment variable

### 2.5 Update UI

- Add "AI Mode" toggle in top bar (rule-based vs. LLM-powered)
- Show which mode is active

### 2.6 Files Changed

| File | Change |
|---|---|
| `virtual_band/llm_agent.py` | **New** — LLM-powered agent base class |
| `virtual_band/prompts.py` | **New** — System prompts and context formatting |
| `virtual_band/orchestrator.py` | Add `use_llm` flag, agent factory |
| `virtual_band/__init__.py` | Export new classes |
| `web/app.py` | Accept LLM toggle, pass config |
| `web/static/app.js` | AI mode toggle UI |
| `web/static/index.html` | Toggle element |
| `web/static/style.css` | Toggle styling |
| `tests/test_llm_agent.py` | **New** — Tests with mocked LLM calls |

---

## Phase 3: Live User Interaction

**Goal**: Let the user join the band as a performer and director.

### 3.1 Add `UserAgent` to `virtual_band/agents.py`

- New `UserAgent(MusicianAgent)` — doesn't auto-play, only emits events received from the client
- `play_tick()` returns any queued events from user input
- Thread-safe event queue (asyncio.Queue)

### 3.2 Backend: User Input via WebSocket

- Extend WebSocket protocol with client→server messages:
  - `{kind: "user_note", pitch: int, velocity: int, duration: int}` — user plays a note
  - `{kind: "user_chord", chord: str}` — user changes the chord
  - `{kind: "user_direction", text: str}` — natural language direction (requires Phase 2 LLM)
- On `user_note`: queue event into `UserAgent`
- On `user_chord`: publish CHORD_CHANGE event to EventBus
- On `user_direction`: broadcast to LLM agents as context modifier (e.g., "The conductor says: play softer")

### 3.3 Frontend: Input Methods

- **Keyboard piano**: Map computer keys (A-L = C4-C5, W-P = sharps) to note triggers
- **Click on piano roll**: Click at (pitch, time) to place a note
- **Direction input**: Text field in top bar — type "take a solo" or "build intensity" and send as `user_direction`
- **MIDI input** (stretch): Use Web MIDI API to receive external MIDI controller input

### 3.4 Files Changed

| File | Change |
|---|---|
| `virtual_band/agents.py` | Add `UserAgent` class |
| `web/app.py` | Handle incoming user messages on WebSocket |
| `web/static/app.js` | Keyboard handler, click-to-play, direction input |
| `web/static/index.html` | Direction text field, keyboard guide overlay |
| `web/static/style.css` | Input styling, keyboard overlay |
| `tests/test_virtual_band.py` | UserAgent tests |

---

## Phase 4: Agent Memory and Learning

**Goal**: Agents remember past performances and develop musical identity over time.

### 4.1 Create `virtual_band/memory.py` — Agent Memory System

- `AgentMemory` class with:
  - `short_term`: Current performance context (already exists as `PerceivedState.recent_events`)
  - `long_term`: Persistent storage of performance summaries
- After each performance, generate a summary:
  - Patterns that worked well (high density alignment, good transitions)
  - Favorite phrases (recurring pitch sequences)
  - Agent interaction stats (who responded to whom)
- Storage: JSON files in `data/memories/{agent_name}.json`

### 4.2 Create `virtual_band/analyzer.py` — Performance Analyzer

- Post-performance analysis:
  - Identify recurring motifs (pitch sequence matching)
  - Score agent interactions (response timing, harmonic consonance)
  - Rate section transitions (smooth vs. abrupt)
- Feed analysis results into agent memory

### 4.3 Integrate Memory into Agents

- On `MusicianAgent.__init__()`, load memory from disk
- In `play_tick()`, reference memory for pattern preferences
- In LLM agents (Phase 2), include memory summary in context prompt
- After `BandOrchestrator.perform()`, save updated memory

### 4.4 Files Changed

| File | Change |
|---|---|
| `virtual_band/memory.py` | **New** — Memory system |
| `virtual_band/analyzer.py` | **New** — Performance analyzer |
| `virtual_band/agents.py` | Load/save memory, reference in play_tick |
| `virtual_band/orchestrator.py` | Post-performance memory update |
| `virtual_band/__init__.py` | Export new classes |
| `tests/test_memory.py` | **New** — Memory and analyzer tests |

---

## Phase 5: Multi-Room Collaborative Sessions

**Goal**: Multiple users watch/control the same performance from different browsers.

### 5.1 Create `web/rooms.py` — Room Management

- `Room` class:
  - Unique room ID (short hash)
  - Connected WebSocket clients list
  - Shared `BandOrchestrator` instance
  - Room state (waiting / performing / paused)
  - Owner (first connected client) can start/stop
- `RoomManager`:
  - Create/join/leave rooms
  - List active rooms
  - Cleanup empty rooms

### 5.2 Update `web/app.py`

- New endpoints:
  - `POST /api/rooms` — Create a room, returns room ID
  - `GET /api/rooms` — List active rooms
  - `GET /api/rooms/{id}` — Room info
- Update WebSocket:
  - `ws/room/{room_id}` — Join a room's session
  - Broadcast all events to all clients in room
  - Handle per-client user input (each client can be a UserAgent)
- Assign agent control: clients can claim an agent to control

### 5.3 Update Frontend

- Room lobby UI:
  - Create room button → generates shareable link
  - Join room input (paste room ID)
  - Room member list showing who controls which agent
- Agent claim buttons: "Take control of Drummer"
- Multi-cursor: Show other users' interactions on piano roll

### 5.4 Files Changed

| File | Change |
|---|---|
| `web/rooms.py` | **New** — Room management |
| `web/app.py` | Room endpoints, room-aware WebSocket |
| `web/static/app.js` | Room lobby, agent claiming, multi-user display |
| `web/static/index.html` | Room UI elements |
| `web/static/style.css` | Room/lobby styling |
| `tests/test_rooms.py` | **New** — Room management tests |

---

## Phase 6: Song Authoring UI

**Goal**: Visual editor for creating custom song structures.

### 6.1 Create Song Editor Component in `app.js`

- **Timeline view**: Horizontal track of sections as draggable blocks
  - Each block shows: section name, chord, duration, intensity
  - Drag to reorder, resize handles to change duration
  - Click to edit section properties
- **Section palette**: Sidebar with section types (intro, verse, chorus, bridge, outro) — drag onto timeline
- **Chord picker**: Dropdown/grid of available chords (pulled from agents' chord maps)
- **Intensity curve editor**: Draw intensity envelope across the song (canvas-based bezier curve editor)
- **Presets**: Save/load song structures (localStorage + optional server-side)

### 6.2 Update Backend

- `POST /api/songs` — Save a named song structure
- `GET /api/songs` — List saved songs
- `GET /api/songs/{name}` — Load a song
- Accept custom `parts` array in WebSocket handshake (already partially supported)

### 6.3 Song Data Model Enhancement

- Add `virtual_band/songs.py`:
  - `Song` dataclass with name, author, parts, created_at
  - Save/load from JSON files in `data/songs/`
  - Validation (valid chords, reasonable durations, intensity range)

### 6.4 Files Changed

| File | Change |
|---|---|
| `virtual_band/songs.py` | **New** — Song data model and persistence |
| `web/app.py` | Song CRUD endpoints |
| `web/static/app.js` | Song editor component (timeline, drag-drop, chord picker, intensity editor) |
| `web/static/index.html` | Editor panel, section palette |
| `web/static/style.css` | Editor styling |
| `tests/test_songs.py` | **New** — Song model tests |

---

## Implementation Order & Dependencies

```
Phase 1: Audio Output          ← No dependencies, highest impact
    ↓
Phase 6: Song Authoring UI     ← No dependencies, unlocks creativity
    ↓
Phase 3: Live User Interaction ← Benefits from audio (Phase 1)
    ↓
Phase 2: LLM-Powered Agents   ← Benefits from user interaction (Phase 3)
    ↓
Phase 4: Agent Memory          ← Requires agent system (Phase 2 optional)
    ↓
Phase 5: Multi-Room Sessions   ← Benefits from all above
```

Phases 1 and 6 can be built in parallel. Phase 3 is best after Phase 1 (so users hear what they play). Phase 2 can start anytime but is most impactful after Phase 3. Phase 4 builds on the agent system. Phase 5 is the capstone that ties everything together.

---

## New File Summary

| File | Phase | Purpose |
|---|---|---|
| `web/static/audio.js` | 1 | Web Audio API synthesizer engine |
| `virtual_band/llm_agent.py` | 2 | LLM-powered musician agents |
| `virtual_band/prompts.py` | 2 | Agent personality prompts |
| `virtual_band/memory.py` | 4 | Persistent agent memory |
| `virtual_band/analyzer.py` | 4 | Performance analysis |
| `virtual_band/songs.py` | 6 | Song data model and persistence |
| `web/rooms.py` | 5 | Multi-room session management |
| `tests/test_llm_agent.py` | 2 | LLM agent tests |
| `tests/test_memory.py` | 4 | Memory system tests |
| `tests/test_rooms.py` | 5 | Room management tests |
| `tests/test_songs.py` | 6 | Song model tests |
