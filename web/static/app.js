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
let allEvents = [];     // [{tick, type, source, pitch, velocity, duration, chord, section, meta}]
let agentNotes = { Drummer: 0, Bassist: 0, Pianist: 0, Vocalist: 0 };
let totalNotes = 0;
let intensityByTick = {};
let densityByTick = {};

/* Per-agent rolling waveform buffers (last 60 velocity values) */
const WAVE_LEN = 60;
let agentWaves = {};
for (const a of Object.keys(AGENT_COLORS)) {
  if (a !== "Orchestrator") agentWaves[a] = new Array(WAVE_LEN).fill(0);
}

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

function startPerformance() {
  if (isPlaying) return;
  resetState();
  isPlaying = true;
  btnPlay.disabled = true;
  btnStop.disabled = false;

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/perform`);

  ws.onopen = () => {
    ws.send(JSON.stringify({ tempo: parseInt(tempoIn.value, 10) || 120 }));
  };

  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.kind === "meta") {
      totalTicks = data.total_ticks;
      tickCtr.textContent = `0 / ${totalTicks}`;
    } else if (data.kind === "tick") {
      handleTick(data);
    } else if (data.kind === "done") {
      $("#metric-total-events").textContent = data.total_events;
      finishPerformance();
    } else if (data.kind === "error") {
      console.error("Server error:", data.message);
      finishPerformance();
    }
  };

  ws.onclose = () => finishPerformance();
  ws.onerror = () => finishPerformance();
}

function stopPerformance() {
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
  // find which block spans the current tick
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

/* ── Piano roll drawing ──────────────────────────────────────── */
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
  ctx.strokeStyle = "rgba(42,45,62,.6)";
  ctx.lineWidth = 1;
  for (let t = 0; t <= totalTicks; t += 4) {
    const x = Math.round(t * colW);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }

  // Horizontal pitch guides (every octave)
  ctx.strokeStyle = "rgba(42,45,62,.35)";
  for (let p = PITCH_MIN; p <= PITCH_MAX; p++) {
    if (p % 12 === 0) {
      const y = h - ((p - PITCH_MIN) / PITCH_RANGE) * h;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }
  }

  // Draw notes
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

    ctx.fillStyle = color;
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    ctx.roundRect(x + 0.5, y + 0.5, noteW - 1, noteH - 1, 1.5 * pr);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
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
  await loadSong();
  resizeCanvases();
  render();
})();
