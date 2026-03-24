/* ── Tone.js Audio Engine for Virtual Band ──────────────────────── */
"use strict";

/**
 * AudioEngine wraps Tone.js to provide instrument synthesis for each agent.
 * Uses look-ahead scheduling: notes are scheduled slightly ahead of time
 * using Tone.js Transport's high-precision clock.
 */
class AudioEngine {
  constructor() {
    this.ready = false;
    this.masterVolume = null;
    this.agentVolumes = {};
    this.instruments = {};
    this.muted = {};
    this.soloAgent = null;
    this._reverbSend = null;
  }

  async init() {
    if (this.ready) return;

    // Ensure Tone.js is loaded
    if (typeof Tone === "undefined") {
      console.warn("Tone.js not loaded, audio disabled");
      return;
    }

    await Tone.start();

    // Master volume
    this.masterVolume = new Tone.Volume(-6).toDestination();

    // Reverb send (shared)
    this._reverbSend = new Tone.Reverb({ decay: 1.5, wet: 0.2 }).connect(this.masterVolume);
    await this._reverbSend.ready;

    // Per-agent volume nodes
    for (const name of ["Drummer", "Bassist", "Pianist", "Vocalist"]) {
      this.agentVolumes[name] = new Tone.Volume(0).connect(this.masterVolume);
      this.muted[name] = false;
    }

    // ── Drummer: membrane + metal + noise synths ──────────────
    const kick = new Tone.MembraneSynth({
      pitchDecay: 0.05,
      octaves: 6,
      oscillator: { type: "sine" },
      envelope: { attack: 0.001, decay: 0.2, sustain: 0, release: 0.2 },
    }).connect(this.agentVolumes["Drummer"]);

    const snare = new Tone.NoiseSynth({
      noise: { type: "white" },
      envelope: { attack: 0.001, decay: 0.15, sustain: 0, release: 0.1 },
    }).connect(this.agentVolumes["Drummer"]);

    const hihat = new Tone.MetalSynth({
      frequency: 400,
      envelope: { attack: 0.001, decay: 0.06, release: 0.05 },
      harmonicity: 5.1,
      modulationIndex: 32,
      resonance: 4000,
      octaves: 1.5,
    }).connect(this.agentVolumes["Drummer"]);

    const ride = new Tone.MetalSynth({
      frequency: 300,
      envelope: { attack: 0.001, decay: 0.3, release: 0.2 },
      harmonicity: 3,
      modulationIndex: 16,
      resonance: 5000,
      octaves: 1,
    }).connect(this.agentVolumes["Drummer"]);

    const crash = new Tone.MetalSynth({
      frequency: 250,
      envelope: { attack: 0.001, decay: 1.0, release: 0.5 },
      harmonicity: 5.1,
      modulationIndex: 40,
      resonance: 3500,
      octaves: 2,
    }).connect(this.agentVolumes["Drummer"]);

    this.instruments["Drummer"] = { kick, snare, hihat, ride, crash };

    // ── Bassist: mono synth with low-pass ────────────────────
    this.instruments["Bassist"] = new Tone.MonoSynth({
      oscillator: { type: "sawtooth" },
      filter: { Q: 2, type: "lowpass", rolloff: -24 },
      envelope: { attack: 0.005, decay: 0.1, sustain: 0.6, release: 0.2 },
      filterEnvelope: { attack: 0.01, decay: 0.1, sustain: 0.3, release: 0.2, baseFrequency: 100, octaves: 2.5 },
    }).connect(this.agentVolumes["Bassist"]);

    // ── Pianist: poly synth with slight detune for warmth ────
    this.instruments["Pianist"] = new Tone.PolySynth(Tone.Synth, {
      oscillator: { type: "triangle" },
      envelope: { attack: 0.005, decay: 0.3, sustain: 0.4, release: 0.5 },
      volume: -8,
    }).connect(this.agentVolumes["Pianist"]);
    this.instruments["Pianist"].connect(this._reverbSend);

    // ── Vocalist: FM synth with vibrato for vocal-like tone ──
    this.instruments["Vocalist"] = new Tone.FMSynth({
      harmonicity: 2,
      modulationIndex: 1.5,
      oscillator: { type: "sine" },
      envelope: { attack: 0.05, decay: 0.2, sustain: 0.7, release: 0.4 },
      modulation: { type: "sine" },
      modulationEnvelope: { attack: 0.1, decay: 0.2, sustain: 0.5, release: 0.3 },
    }).connect(this.agentVolumes["Vocalist"]);
    this.instruments["Vocalist"].connect(this._reverbSend);

    this.ready = true;
  }

  /**
   * Play a note for the given agent.
   * @param {string} agent - Agent name
   * @param {number} pitch - MIDI pitch (0-127)
   * @param {number} velocity - MIDI velocity (0-127)
   * @param {number} durationTicks - Duration in ticks
   * @param {number} tempo - Current BPM
   */
  playNote(agent, pitch, velocity, durationTicks, tempo) {
    if (!this.ready) return;

    // Check mute/solo
    if (this.muted[agent]) return;
    if (this.soloAgent && this.soloAgent !== agent) return;

    const freq = 440 * Math.pow(2, (pitch - 69) / 12);
    const vel = Math.max(0.01, velocity / 127);
    const durSec = Math.max(0.05, (durationTicks * 60) / (tempo * 4));
    const now = Tone.now();

    if (agent === "Drummer") {
      this._playDrum(pitch, vel, now);
    } else if (agent === "Bassist") {
      try {
        this.instruments["Bassist"].triggerAttackRelease(freq, durSec, now, vel);
      } catch (e) { /* synth busy */ }
    } else if (agent === "Pianist") {
      try {
        this.instruments["Pianist"].triggerAttackRelease(freq, durSec, now, vel * 0.7);
      } catch (e) { /* synth busy */ }
    } else if (agent === "Vocalist") {
      try {
        this.instruments["Vocalist"].triggerAttackRelease(freq, durSec, now, vel * 0.6);
      } catch (e) { /* synth busy */ }
    }
  }

  _playDrum(pitch, vel, time) {
    const drums = this.instruments["Drummer"];
    if (!drums) return;

    // Map MIDI pitch to drum sound
    if (pitch === 36) {
      // Kick
      drums.kick.triggerAttackRelease("C1", "8n", time, vel);
    } else if (pitch === 38) {
      // Snare
      drums.snare.triggerAttackRelease("8n", time, vel * 0.8);
    } else if (pitch === 42) {
      // Hi-hat closed
      drums.hihat.triggerAttackRelease("32n", time, vel * 0.3);
    } else if (pitch === 46) {
      // Hi-hat open
      drums.hihat.triggerAttackRelease("8n", time, vel * 0.4);
    } else if (pitch === 51) {
      // Ride
      drums.ride.triggerAttackRelease("4n", time, vel * 0.25);
    } else if (pitch === 49) {
      // Crash
      drums.crash.triggerAttackRelease("2n", time, vel * 0.35);
    }
  }

  /** Set master volume in dB (-60 to 0). */
  setMasterVolume(db) {
    if (this.masterVolume) {
      this.masterVolume.volume.value = db;
    }
  }

  /** Set per-agent volume in dB. */
  setAgentVolume(agent, db) {
    if (this.agentVolumes[agent]) {
      this.agentVolumes[agent].volume.value = db;
    }
  }

  /** Mute/unmute an agent. */
  setMute(agent, muted) {
    this.muted[agent] = muted;
  }

  /** Solo an agent (null to unsolo). */
  setSolo(agent) {
    this.soloAgent = agent;
  }

  /** Dispose all synths (cleanup). */
  dispose() {
    if (!this.ready) return;
    for (const key of Object.keys(this.instruments)) {
      const inst = this.instruments[key];
      if (inst && typeof inst.dispose === "function") {
        inst.dispose();
      } else if (inst && typeof inst === "object") {
        Object.values(inst).forEach(s => s && typeof s.dispose === "function" && s.dispose());
      }
    }
    if (this._reverbSend) this._reverbSend.dispose();
    if (this.masterVolume) this.masterVolume.dispose();
    this.ready = false;
  }
}

// Global instance
const audioEngine = new AudioEngine();
