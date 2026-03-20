# Virtual Band — Phase 2 Implementation Plan

## Problem
Agents are independent pattern players. They don't truly listen, react, or coordinate. Intensity never changes, unused event types sit idle, and the song structure is rigid.

## Plan (6 steps, ordered by impact)

---

### Step 1: Emit BEAT events from the Orchestrator + use DYNAMIC_CHANGE
**Why:** Agents currently infer the beat from `tick % 4`. An explicit BEAT event gives them a shared rhythmic anchor. And intensity is stuck at 80 — nothing ever emits DYNAMIC_CHANGE.

**Changes:**
- `orchestrator.py`: Emit a `BEAT` event every tick with beat position and bar number in `meta`
- `orchestrator.py`: Emit `DYNAMIC_CHANGE` events at section boundaries (e.g., chorus louder, bridge softer, outro fade)
- `SongStructure.parts` tuples gain a 4th element: target intensity (0–127)

---

### Step 2: Add a shared BandState visible to all agents
**Why:** Agents can't see what others are doing. A read-only shared state lets them coordinate without tight coupling.

**Changes:**
- New class `BandState` in `events.py` (or new file `band_state.py`) tracking:
  - Per-agent last event and current activity (playing/resting)
  - Aggregate density (how many agents played this tick)
  - Current soloist (if any)
- `EventBus.publish()` updates `BandState` automatically
- Agents receive a reference to `BandState` at construction

---

### Step 3: Make agents truly reactive — meaningful `react()` implementations
**Why:** Drummer's `react()` is empty. Bassist/Pianist/Vocalist barely react. This is the core "listening" gap.

**Changes:**
- **Drummer**: React to `DYNAMIC_CHANGE` by switching between patterns (sparse vs. busy). React to Vocalist resting by adding fills.
- **Bassist**: Check `BandState.density` — if density is high, simplify to roots only. If Vocalist is resting, add melodic walk-ups. Lock tighter to Drummer's kick pattern.
- **Pianist**: Vary voicing density by section (sparse in verse, full in chorus). When Vocalist is singing, stay out of the vocal register. React to `DYNAMIC_CHANGE` with velocity shifts.
- **Vocalist**: Adapt phrasing to section (longer notes in intro/outro, more active in chorus). Rest more when Pianist is comping heavily. Sing call-and-response with rests after bass fills.

---

### Step 4: Section-aware agent strategies
**Why:** Every agent plays the same pattern regardless of section. Real musicians play differently in a verse vs. chorus vs. bridge.

**Changes:**
- Add a `strategy` pattern to `MusicianAgent` — a dict mapping section names to behavior parameters (density, velocity range, pattern selection)
- Each concrete agent defines its own strategy map:
  - Drummer: brushes in verse, full kit in chorus, half-time in bridge
  - Bassist: whole notes in intro, walking in verse, driving eighths in chorus
  - Pianist: arpeggios in verse, block chords in chorus, sparse in bridge
  - Vocalist: melody in verse/chorus, rest in intro, sustained notes in outro

---

### Step 5: Add call-and-response / interaction patterns
**Why:** The most musically interesting behavior — agents responding directly to each other's phrases.

**Changes:**
- New `InteractionPattern` base class with concrete implementations:
  - `CallAndResponse`: When one agent plays a phrase and rests, another picks up
  - `LockIn`: Bassist + Drummer align rhythmically (bass plays on kick hits)
  - `DropOut`: Agents selectively drop out to feature a soloist
- `BandOrchestrator` can assign interaction patterns to agent pairs
- Agents check active interaction patterns in their `play_tick()` to modify behavior

---

### Step 6: Expand tests for agent interaction
**Why:** Current tests verify agents in isolation. Need to verify the band actually coheres.

**Changes:**
- Integration tests verifying:
  - Intensity actually changes across sections
  - Bassist follows Drummer's kick pattern when LockIn is active
  - Vocalist rests more when density is high
  - Call-and-response produces alternating phrase patterns
  - Agents play differently in verse vs. chorus
- Add a `PerformanceAnalyzer` utility that computes metrics from event history:
  - Density per tick
  - Harmonic consonance (are pitches within the current chord?)
  - Rhythmic alignment (do bass/drum hits coincide?)
