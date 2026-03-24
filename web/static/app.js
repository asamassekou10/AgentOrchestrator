/* ── Virtual Band UI ─────────────────────────────────────────── */
"use strict";

const AGENT_COLORS = {
  Drummer:      "#f59e0b",
  Bassist:      "#10b981",
  Pianist:      "#6366f1",
  Vocalist:     "#ec4899",
  Orchestrator: "#64748b",
};

const NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
function midiToName(m) {
  if (m == null) return "";
  return NOTE_NAMES[m % 12] + (Math.floor(m / 12) - 1);
}

/* ── State ───────────────────────────────────────────────────── */
let ws = null;
let isPlaying = false;
let totalTicks = 0;
let currentTick = -1;
let songParts = [];
let allEvents = [];
let agentNotes = { Drummer: 0, Bassist: 0, Pianist: 0, Vocalist: 0 };
let totalNotes = 0;
let intensityByTick = {};
let densityByTick = {};
let currentPerformanceId = null;
let currentTempo = 120;
let llmAvailable = false;

/* Per-agent rolling waveform buffers (last 60 velocity values) */
const WAVE_LEN = 60;
let agentWaves = {};
for (const a of Object.keys(AGENT_COLORS)) {
  if (a !== "Orchestrator") agentWaves[a] = new Array(WAVE_LEN).fill(0);
}

/* Audio state */
let audioEnabled = false;

/* Reconnection state */
let reconnectAttempts = 0;
const MAX_RECONNECT = 3;

/* ── DOM refs ────────────────────────────────────────────────── */
const $ = (sel) => document.querySelector(sel);
const btnPlay  = $("#btn-play");
const btnStop  = $("#btn-stop");
const tempoIn  = $("#tempo");
const barNum   = $("#bar-num");
const beatNum  = $("#beat-num");
const tickCtr  = $("#tick-counter");
const playhead = $("#playhead");
const pianoCanvas  = $("#pianoroll");
const intCanvas    = $("#intensity-canvas");
const sectionStrip = $("#section-strip");
const eventLog     = $("#event-log");

/* ── Piano roll setup ────────────────────────────────────────── */
const PITCH_MIN = 30;
const PITCH_MAX = 84;
const PITCH_RANGE = PITCH_MAX - PITCH_MIN;

function resizeCanvases() {
  const pr = window.devicePixelRatio || 1;
  const pWrap = pianoCanvas.parentElement;
  pianoCanvas.width  = pWrap.clientWidth  * pr;
  pianoCanvas.height = pWrap.clientHeight * pr;
  pianoCanvas.style.width  = pWrap.clientWidth  + "px";
  pianoCanvas.style.height = pWrap.clientHeight + "px";

  const iWrap = intCanvas.parentElement;
  intCanvas.width  = iWrap.clientWidth  * pr;
  intCanvas.height = iWrap.clientHeight * pr;
  intCanvas.style.width  = iWrap.clientWidth  + "px";
  intCanvas.style.height = iWrap.clientHeight + "px";

  // Agent waveform canvases
  for (const a of ["Drummer","Bassist","Pianist","Vocalist"]) {
    const c = $(`#canvas-${a}`);
    if (!c) continue;
    const w = c.parentElement;
    c.width  = w.clientWidth  * pr;
    c.height = w.clientHeight * pr;
    c.style.width  = w.clientWidth + "px";
    c.style.height = w.clientHeight + "px";
  }
}
window.addEventListener("resize", () => { resizeCanvases(); render(); });

/* ── Section strip builder ───────────────────────────────────── */
async function loadSong() {
  const res = await fetch("/api/song");
  const data = await res.json();
  songParts = data.parts;
  totalTicks = data.total_ticks;
  buildSectionStrip();
}

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    llmAvailable = data.llm_available;
    if (tempoIn) tempoIn.value = data.default_tempo || 120;
    const llmToggle = $("#llm-toggle");
    if (llmToggle) {
      llmToggle.style.display = llmAvailable ? "flex" : "none";
    }
  } catch (e) { /* config endpoint optional */ }
}

function buildSectionStrip() {
  sectionStrip.innerHTML = "";
  for (const p of songParts) {
    const el = document.createElement("div");
    el.className = `section-block section-${p.section}`;
    el.style.flex = `${p.duration} 0 0`;
    el.dataset.section = p.section;
    el.innerHTML = `${p.section}<span class="chord-label">${p.chord}</span>`;
    sectionStrip.appendChild(el);
  }
}

/* ── WebSocket / Transport ───────────────────────────────────── */
btnPlay.addEventListener("click", startPerformance);
btnStop.addEventListener("click", stopPerformance);

async function startPerformance() {
  if (isPlaying) return;

  // Init audio on first play (requires user gesture)
  if (!audioEnabled && typeof audioEngine !== "undefined") {
    await audioEngine.init();
    audioEnabled = audioEngine.ready;
  }

  resetState();
  isPlaying = true;
  reconnectAttempts = 0;
  btnPlay.disabled = true;
  btnStop.disabled = false;

  currentTempo = parseInt(tempoIn.value, 10) || 120;
  const useLLM = $("#llm-checkbox") ? $("#llm-checkbox").checked : false;

  connectWebSocket(currentTempo, useLLM);
}

function connectWebSocket(tempo, useLLM) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/perform`);

  ws.onopen = () => {
    reconnectAttempts = 0;
    ws.send(JSON.stringify({ tempo, use_llm: useLLM }));
  };

  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.kind === "meta") {
      totalTicks = data.total_ticks;
      tickCtr.textContent = `0 / ${totalTicks}`;
    } else if (data.kind === "tick") {
      handleTick(data);
    } else if (data.kind === "done") {
      currentPerformanceId = data.performance_id;
      $("#metric-total-events").textContent = data.total_events;
      updateExportButtons();
      finishPerformance();
    } else if (data.kind === "error") {
      console.error("Server error:", data.message);
      finishPerformance();
    }
  };

  ws.onclose = () => {
    if (isPlaying && reconnectAttempts < MAX_RECONNECT) {
      reconnectAttempts++;
      console.log(`Reconnecting... attempt ${reconnectAttempts}`);
      setTimeout(() => connectWebSocket(tempo, useLLM), 1000);
    } else {
      finishPerformance();
    }
  };

  ws.onerror = () => {
    // onclose will handle reconnection
  };
}

function stopPerformance() {
  reconnectAttempts = MAX_RECONNECT; // prevent reconnect
  if (ws) { ws.close(); ws = null; }
  finishPerformance();
}

function finishPerformance() {
  isPlaying = false;
  btnPlay.disabled = false;
  btnStop.disabled = true;
  for (const a of ["Drummer","Bassist","Pianist","Vocalist"]) {
    setAgentStatus(a, "idle");
    $(`.agent-panel[data-agent="${a}"]`).classList.remove("active");
  }
}

function resetState() {
  allEvents = [];
  agentNotes = { Drummer: 0, Bassist: 0, Pianist: 0, Vocalist: 0 };
  totalNotes = 0;
  currentTick = -1;
  currentPerformanceId = null;
  intensityByTick = {};
  densityByTick = {};
  for (const a in agentWaves) agentWaves[a].fill(0);
  eventLog.innerHTML = "";
  $("#metric-total-events").textContent = "0";
  $("#metric-total-notes").textContent = "0";
  $("#metric-section").textContent = "--";
  $("#metric-chord").textContent = "--";
  $("#metric-intensity-val").textContent = "0";
  $("#meter-intensity").style.width = "0%";
  $("#metric-density-val").textContent = "0";
  $("#meter-density").style.width = "0%";
  for (const a of ["Drummer","Bassist","Pianist","Vocalist"]) {
    $(`#notes-${a}`).textContent = "0 notes";
    $(`#vel-${a}`).textContent = "vel: --";
  }
  updateExportButtons();
}

/* ── Tick handler ────────────────────────────────────────────── */
function handleTick(data) {
  currentTick = data.tick;
  tickCtr.textContent = `${currentTick + 1} / ${totalTicks}`;
  barNum.textContent = Math.floor(currentTick / 4) + 1;
  beatNum.textContent = (currentTick % 4) + 1;

  let tickDensity = 0;
  let tickIntensity = null;

  const agentPlayedThisTick = {};

  for (const ev of data.events) {
    allEvents.push(ev);

    // Section / chord
    if (ev.type === "section_change" && ev.section) {
      $("#metric-section").textContent = ev.section.toUpperCase();
      highlightSection(ev.section);
    }
    if (ev.type === "chord_change" && ev.chord) {
      $("#metric-chord").textContent = ev.chord;
    }
    if (ev.type === "dynamic_change") {
      tickIntensity = ev.velocity;
    }

    // Notes
    if (ev.type === "note_on" && ev.source !== "Orchestrator") {
      tickDensity++;
      agentNotes[ev.source] = (agentNotes[ev.source] || 0) + 1;
      totalNotes++;
      agentPlayedThisTick[ev.source] = ev.velocity;

      // Waveform push
      if (agentWaves[ev.source]) {
        agentWaves[ev.source].push(ev.velocity);
        if (agentWaves[ev.source].length > WAVE_LEN) agentWaves[ev.source].shift();
      }

      // Play audio
      if (audioEnabled && typeof audioEngine !== "undefined") {
        audioEngine.playNote(ev.source, ev.pitch, ev.velocity, ev.duration || 1, currentTempo);
      }
    }

    // REST
    if (ev.type === "rest" && ev.source !== "Orchestrator") {
      agentPlayedThisTick[ev.source] = agentPlayedThisTick[ev.source] || 0;
    }

    // Log (only non-beat/non-dynamic to reduce noise)
    if (ev.type !== "beat" && ev.type !== "dynamic_change") {
      addLogEntry(ev);
    }
  }

  // Update per-agent UI
  for (const a of ["Drummer","Bassist","Pianist","Vocalist"]) {
    if (agentPlayedThisTick[a] > 0) {
      setAgentStatus(a, "playing");
      $(`.agent-panel[data-agent="${a}"]`).classList.add("active");
      $(`#vel-${a}`).textContent = `vel: ${agentPlayedThisTick[a]}`;
    } else if (a in agentPlayedThisTick) {
      setAgentStatus(a, "resting");
      $(`.agent-panel[data-agent="${a}"]`).classList.remove("active");
    } else {
      // no event for this agent this tick — waveform still push 0
      if (agentWaves[a]) {
        agentWaves[a].push(0);
        if (agentWaves[a].length > WAVE_LEN) agentWaves[a].shift();
      }
    }
    $(`#notes-${a}`).textContent = `${agentNotes[a] || 0} notes`;
  }

  // Intensity
  if (tickIntensity != null) {
    intensityByTick[currentTick] = tickIntensity;
    const pct = Math.round((tickIntensity / 127) * 100);
    $("#meter-intensity").style.width = pct + "%";
    $("#metric-intensity-val").textContent = tickIntensity;
  } else {
    // carry forward
    const keys = Object.keys(intensityByTick).map(Number);
    if (keys.length) intensityByTick[currentTick] = intensityByTick[Math.max(...keys)];
  }

  // Density
  densityByTick[currentTick] = tickDensity;
  const densityPct = Math.min(100, Math.round((tickDensity / 8) * 100));
  $("#meter-density").style.width = densityPct + "%";
  $("#metric-density-val").textContent = tickDensity;

  // Totals
  $("#metric-total-events").textContent = allEvents.length;
  $("#metric-total-notes").textContent = totalNotes;

  // Render
  render();
}

/* ── Section highlight ───────────────────────────────────────── */
function highlightSection(section) {
  let cursor = 0;
  const blocks = sectionStrip.children;
  for (let i = 0; i < songParts.length; i++) {
    blocks[i].classList.remove("current");
    if (currentTick >= cursor && currentTick < cursor + songParts[i].duration) {
      blocks[i].classList.add("current");
    }
    cursor += songParts[i].duration;
  }
}

/* ── Agent status helper ─────────────────────────────────────── */
function setAgentStatus(name, status) {
  const el = $(`#status-${name}`);
  el.textContent = status;
  el.className = "agent-status " + status;
}

/* ── Mute / Solo / Volume controls ───────────────────────────── */
function toggleMute(agent) {
  const btn = $(`#mute-${agent}`);
  const isMuted = btn.classList.toggle("active");
  if (typeof audioEngine !== "undefined" && audioEngine.ready) {
    audioEngine.setMute(agent, isMuted);
  }
  // Visual: dim the agent panel
  const panel = $(`.agent-panel[data-agent="${agent}"]`);
  panel.classList.toggle("muted", isMuted);
}

function toggleSolo(agent) {
  const btn = $(`#solo-${agent}`);
  const wasSolo = btn.classList.contains("active");

  // Clear all solo buttons
  for (const a of ["Drummer","Bassist","Pianist","Vocalist"]) {
    $(`#solo-${a}`).classList.remove("active");
    $(`.agent-panel[data-agent="${a}"]`).classList.remove("soloed-out");
  }

  if (wasSolo) {
    // Unsolo
    if (typeof audioEngine !== "undefined" && audioEngine.ready) {
      audioEngine.setSolo(null);
    }
  } else {
    // Solo this agent
    btn.classList.add("active");
    if (typeof audioEngine !== "undefined" && audioEngine.ready) {
      audioEngine.setSolo(agent);
    }
    // Dim other panels
    for (const a of ["Drummer","Bassist","Pianist","Vocalist"]) {
      if (a !== agent) {
        $(`.agent-panel[data-agent="${a}"]`).classList.add("soloed-out");
      }
    }
  }
}

function setAgentVolume(agent, value) {
  // value is 0-100, map to dB (-40 to 0)
  const db = value === 0 ? -60 : -40 + (value / 100) * 40;
  if (typeof audioEngine !== "undefined" && audioEngine.ready) {
    audioEngine.setAgentVolume(agent, db);
  }
}

function setMasterVolume(value) {
  const db = value === 0 ? -60 : -40 + (value / 100) * 40;
  if (typeof audioEngine !== "undefined" && audioEngine.ready) {
    audioEngine.setMasterVolume(db);
  }
}

/* ── Export buttons ──────────────────────────────────────────── */
function updateExportButtons() {
  const hasPerf = currentPerformanceId != null;
  const midiBtn = $("#btn-midi-export");
  if (midiBtn) midiBtn.disabled = !hasPerf;
}

function exportMidi() {
  if (!currentPerformanceId) return;
  window.open(`/api/performances/${currentPerformanceId}/midi`, "_blank");
}

/* ── Performance history ─────────────────────────────────────── */
async function loadPerformanceHistory() {
  try {
    const res = await fetch("/api/performances");
    const data = await res.json();
    const list = $("#performance-list");
    if (!list) return;
    list.innerHTML = "";
    for (const p of data.performances) {
      const el = document.createElement("div");
      el.className = "perf-item";
      el.innerHTML = `<span>${p.name}</span><span class="perf-events">${p.total_events} events</span>`;
      el.addEventListener("click", () => replayPerformance(p.id));
      list.appendChild(el);
    }
  } catch (e) { /* performances endpoint optional */ }
}

async function replayPerformance(perfId) {
  if (isPlaying) return;

  // Init audio on first replay
  if (!audioEnabled && typeof audioEngine !== "undefined") {
    await audioEngine.init();
    audioEnabled = audioEngine.ready;
  }

  try {
    const res = await fetch(`/api/performances/${perfId}`);
    const perf = await res.json();
    if (perf.error) return;

    resetState();
    isPlaying = true;
    btnPlay.disabled = true;
    btnStop.disabled = false;

    currentTempo = perf.tempo || 120;
    totalTicks = perf.total_ticks;
    currentPerformanceId = perf.id;
    tickCtr.textContent = `0 / ${totalTicks}`;

    // Group events by tick
    const eventsByTick = {};
    for (const ev of perf.events) {
      const t = ev.tick;
      if (!eventsByTick[t]) eventsByTick[t] = [];
      eventsByTick[t].push(ev);
    }

    // Replay tick-by-tick
    for (let tick = 0; tick < totalTicks; tick++) {
      if (!isPlaying) break;
      const tickEvents = eventsByTick[tick] || [];
      handleTick({ tick, events: tickEvents });
      await new Promise(r => setTimeout(r, 60000 / currentTempo / 4));
    }

    updateExportButtons();
    finishPerformance();
  } catch (e) {
    console.error("Replay error:", e);
    finishPerformance();
  }
}

/* ── Song authoring ──────────────────────────────────────────── */
const SECTIONS = ["intro", "verse", "chorus", "bridge", "outro"];
const CHORDS = [
  "C", "Cm", "Cmaj7", "Cm7", "D", "Dm", "Dmaj7", "Dm7",
  "E", "Em", "F", "Fm", "Fmaj7", "Fm7", "G", "Gm", "Gmaj7", "Gm7",
  "A", "Am", "Amaj7", "Am7", "B", "Bm",
];

let editingSong = [];

function openSongEditor() {
  const modal = $("#song-editor-modal");
  if (!modal) return;
  // Pre-populate with current song parts
  editingSong = songParts.map(p => ({ ...p }));
  renderSongEditor();
  modal.classList.add("visible");
}

function closeSongEditor() {
  const modal = $("#song-editor-modal");
  if (modal) modal.classList.remove("visible");
}

function renderSongEditor() {
  const container = $("#song-parts-editor");
  if (!container) return;
  container.innerHTML = "";

  editingSong.forEach((part, idx) => {
    const row = document.createElement("div");
    row.className = "song-part-row";
    row.innerHTML = `
      <select class="se-section" data-idx="${idx}">
        ${SECTIONS.map(s => `<option value="${s}" ${s === part.section ? "selected" : ""}>${s}</option>`).join("")}
      </select>
      <select class="se-chord" data-idx="${idx}">
        ${CHORDS.map(c => `<option value="${c}" ${c === part.chord ? "selected" : ""}>${c}</option>`).join("")}
      </select>
      <input type="number" class="se-duration" data-idx="${idx}" value="${part.duration}" min="1" max="64" title="Duration (ticks)">
      <input type="range" class="se-intensity" data-idx="${idx}" value="${part.intensity}" min="0" max="127" title="Intensity">
      <span class="se-intensity-val">${part.intensity}</span>
      <button class="btn-icon se-remove" data-idx="${idx}" title="Remove">&times;</button>
    `;
    container.appendChild(row);
  });

  // Wire events
  container.querySelectorAll(".se-section").forEach(el => {
    el.addEventListener("change", e => { editingSong[+e.target.dataset.idx].section = e.target.value; });
  });
  container.querySelectorAll(".se-chord").forEach(el => {
    el.addEventListener("change", e => { editingSong[+e.target.dataset.idx].chord = e.target.value; });
  });
  container.querySelectorAll(".se-duration").forEach(el => {
    el.addEventListener("change", e => { editingSong[+e.target.dataset.idx].duration = parseInt(e.target.value) || 4; });
  });
  container.querySelectorAll(".se-intensity").forEach(el => {
    el.addEventListener("input", e => {
      const idx = +e.target.dataset.idx;
      editingSong[idx].intensity = parseInt(e.target.value);
      e.target.nextElementSibling.textContent = e.target.value;
    });
  });
  container.querySelectorAll(".se-remove").forEach(el => {
    el.addEventListener("click", e => {
      editingSong.splice(+e.target.dataset.idx, 1);
      renderSongEditor();
    });
  });
}

function addSongPart() {
  editingSong.push({ section: "verse", chord: "C", duration: 8, intensity: 80 });
  renderSongEditor();
}

function applySongEdit() {
  if (editingSong.length === 0) return;
  songParts = editingSong.map(p => ({ ...p }));
  totalTicks = songParts.reduce((s, p) => s + p.duration, 0);
  buildSectionStrip();
  closeSongEditor();
}

async function saveSongToServer() {
  const nameInput = $("#song-name-input");
  const name = nameInput ? nameInput.value.trim() : "";
  if (!name) { alert("Enter a song name"); return; }
  try {
    await fetch("/api/songs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, parts: editingSong }),
    });
    await loadSavedSongs();
  } catch (e) { console.error(e); }
}

async function loadSavedSongs() {
  try {
    const res = await fetch("/api/songs");
    const data = await res.json();
    const list = $("#saved-songs-list");
    if (!list) return;
    list.innerHTML = "";
    for (const name of data.songs) {
      const el = document.createElement("div");
      el.className = "saved-song-item";
      el.textContent = name;
      el.addEventListener("click", () => loadSongByName(name));
      list.appendChild(el);
    }
  } catch (e) { /* optional */ }
}

async function loadSongByName(name) {
  try {
    const res = await fetch(`/api/songs/${encodeURIComponent(name)}`);
    const data = await res.json();
    if (data.parts) {
      editingSong = data.parts;
      renderSongEditor();
    }
  } catch (e) { console.error(e); }
}

/* ── Event log ───────────────────────────────────────────────── */
function addLogEntry(ev) {
  const div = document.createElement("div");
  div.className = "log-entry";
  const srcClass = `src-${ev.source.toLowerCase()}`;
  let detail = ev.type;
  if (ev.pitch != null) detail += ` ${midiToName(ev.pitch)}`;
  if (ev.chord) detail += ` ${ev.chord}`;
  if (ev.section) detail += ` ${ev.section}`;
  div.innerHTML = `<span style="color:var(--text-muted)">${ev.tick}</span> <span class="${srcClass}">${ev.source.substring(0,4)}</span> ${detail}`;
  eventLog.prepend(div);
  // cap
  while (eventLog.children.length > 200) eventLog.removeChild(eventLog.lastChild);
}

/* ── Render ──────────────────────────────────────────────────── */
function render() {
  drawPianoRoll();
  drawIntensity();
  drawAgentWaveforms();
  updatePlayhead();
}

/* ── Playhead ────────────────────────────────────────────────── */
function updatePlayhead() {
  if (totalTicks <= 0 || currentTick < 0) { playhead.style.left = "0px"; return; }
  const wrap = pianoCanvas.parentElement;
  const pct = (currentTick + 1) / totalTicks;
  playhead.style.left = (pct * wrap.clientWidth) + "px";
}

/* ── Piano roll drawing (improved with note labels and velocity shading) ── */
function drawPianoRoll() {
  const canvas = pianoCanvas;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  const pr = window.devicePixelRatio || 1;

  ctx.clearRect(0, 0, w, h);

  if (totalTicks <= 0) return;

  const colW = w / totalTicks;
  const rowH = h / PITCH_RANGE;

  // Grid lines (every 4 ticks = bar line)
  ctx.lineWidth = 1;
  for (let t = 0; t <= totalTicks; t += 4) {
    const x = Math.round(t * colW);
    ctx.strokeStyle = t % 16 === 0 ? "rgba(42,45,62,.8)" : "rgba(42,45,62,.4)";
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }

  // Horizontal pitch guides (every octave) with labels
  ctx.font = `${9 * pr}px monospace`;
  for (let p = PITCH_MIN; p <= PITCH_MAX; p++) {
    if (p % 12 === 0) {
      const y = h - ((p - PITCH_MIN) / PITCH_RANGE) * h;
      ctx.strokeStyle = "rgba(42,45,62,.35)";
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
      // Octave label
      ctx.fillStyle = "rgba(136,146,168,.5)";
      ctx.fillText(`C${Math.floor(p / 12) - 1}`, 3 * pr, y - 2 * pr);
    }
  }

  // Draw notes with rounded rects and glow effect
  for (const ev of allEvents) {
    if (ev.type !== "note_on" || ev.pitch == null) continue;
    if (ev.source === "Orchestrator") continue;

    const color = AGENT_COLORS[ev.source] || "#888";
    const x = ev.tick * colW;
    const noteW = Math.max(colW * (ev.duration || 1), 2 * pr);
    const y = h - ((ev.pitch - PITCH_MIN) / PITCH_RANGE) * h - rowH;
    const noteH = Math.max(rowH, 2 * pr);

    // Velocity → alpha
    const alpha = 0.35 + (ev.velocity / 127) * 0.65;

    // Glow for high-velocity notes
    if (ev.velocity > 100) {
      ctx.shadowColor = color;
      ctx.shadowBlur = 4 * pr;
    }

    ctx.fillStyle = color;
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    ctx.roundRect(x + 0.5, y + 0.5, noteW - 1, noteH - 1, 1.5 * pr);
    ctx.fill();

    ctx.shadowBlur = 0;
  }
  ctx.globalAlpha = 1;

  // Playback cursor line
  if (currentTick >= 0) {
    const cx = (currentTick + 0.5) * colW;
    ctx.strokeStyle = "rgba(99,102,241,.4)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4 * pr, 4 * pr]);
    ctx.beginPath();
    ctx.moveTo(cx, 0);
    ctx.lineTo(cx, h);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

/* ── Intensity curve ─────────────────────────────────────────── */
function drawIntensity() {
  const ctx = intCanvas.getContext("2d");
  const w = intCanvas.width;
  const h = intCanvas.height;
  const pr = window.devicePixelRatio || 1;
  ctx.clearRect(0, 0, w, h);

  if (totalTicks <= 0) return;
  const colW = w / totalTicks;

  // Background bar lines
  ctx.strokeStyle = "rgba(42,45,62,.4)";
  ctx.lineWidth = 1;
  for (let t = 0; t <= totalTicks; t += 4) {
    const x = Math.round(t * colW);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }

  // Intensity fill
  const grad = ctx.createLinearGradient(0, h, 0, 0);
  grad.addColorStop(0, "rgba(99,102,241,0.05)");
  grad.addColorStop(1, "rgba(99,102,241,0.35)");

  ctx.beginPath();
  ctx.moveTo(0, h);
  let lastVal = 0;
  for (let t = 0; t <= currentTick; t++) {
    const val = intensityByTick[t] ?? lastVal;
    lastVal = val;
    const x = (t + 0.5) * colW;
    const y = h - (val / 127) * h;
    if (t === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.lineTo((currentTick + 0.5) * colW, h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Intensity line
  ctx.beginPath();
  lastVal = 0;
  for (let t = 0; t <= currentTick; t++) {
    const val = intensityByTick[t] ?? lastVal;
    lastVal = val;
    const x = (t + 0.5) * colW;
    const y = h - (val / 127) * h;
    if (t === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = "#6366f1";
  ctx.lineWidth = 2 * pr;
  ctx.stroke();

  // Density dots
  for (let t = 0; t <= currentTick; t++) {
    const d = densityByTick[t] || 0;
    if (d === 0) continue;
    const x = (t + 0.5) * colW;
    const y = h - 4 * pr;
    ctx.fillStyle = `rgba(245,158,11,${Math.min(1, d / 6)})`;
    ctx.beginPath();
    ctx.arc(x, y, Math.min(3 * pr, d * pr), 0, Math.PI * 2);
    ctx.fill();
  }
}

/* ── Agent waveform canvases ─────────────────────────────────── */
function drawAgentWaveforms() {
  for (const a of ["Drummer","Bassist","Pianist","Vocalist"]) {
    const canvas = $(`#canvas-${a}`);
    if (!canvas) continue;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const wave = agentWaves[a];
    if (!wave) continue;
    const color = AGENT_COLORS[a];
    const pr = window.devicePixelRatio || 1;
    const slotW = w / WAVE_LEN;

    // Bar style — thin velocity bars
    for (let i = 0; i < wave.length; i++) {
      const v = wave[i];
      if (v <= 0) continue;
      const barH = (v / 127) * h;
      const x = i * slotW;
      const alpha = 0.3 + (v / 127) * 0.7;
      ctx.fillStyle = color;
      ctx.globalAlpha = alpha;
      ctx.fillRect(x, h - barH, Math.max(slotW - 1, 1), barH);
    }
    ctx.globalAlpha = 1;

    // Baseline
    ctx.strokeStyle = "rgba(255,255,255,.06)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, h);
    ctx.lineTo(w, h);
    ctx.stroke();
  }
}

/* ── Init ────────────────────────────────────────────────────── */
(async function init() {
  await loadConfig();
  await loadSong();
  resizeCanvases();
  render();
  loadPerformanceHistory();
  loadSavedSongs();

  // Wire song editor buttons
  const btnEditSong = $("#btn-edit-song");
  if (btnEditSong) btnEditSong.addEventListener("click", openSongEditor);
  const btnCloseSE = $("#btn-close-song-editor");
  if (btnCloseSE) btnCloseSE.addEventListener("click", closeSongEditor);
  const btnAddPart = $("#btn-add-part");
  if (btnAddPart) btnAddPart.addEventListener("click", addSongPart);
  const btnApplySong = $("#btn-apply-song");
  if (btnApplySong) btnApplySong.addEventListener("click", applySongEdit);
  const btnSaveSong = $("#btn-save-song");
  if (btnSaveSong) btnSaveSong.addEventListener("click", saveSongToServer);
  const btnExportMidi = $("#btn-midi-export");
  if (btnExportMidi) btnExportMidi.addEventListener("click", exportMidi);
  const masterVol = $("#master-volume");
  if (masterVol) masterVol.addEventListener("input", e => setMasterVolume(+e.target.value));

  // Wire per-agent controls
  for (const a of ["Drummer","Bassist","Pianist","Vocalist"]) {
    const muteBtn = $(`#mute-${a}`);
    if (muteBtn) muteBtn.addEventListener("click", () => toggleMute(a));
    const soloBtn = $(`#solo-${a}`);
    if (soloBtn) soloBtn.addEventListener("click", () => toggleSolo(a));
    const volSlider = $(`#vol-${a}`);
    if (volSlider) volSlider.addEventListener("input", e => setAgentVolume(a, +e.target.value));
  }
})();
