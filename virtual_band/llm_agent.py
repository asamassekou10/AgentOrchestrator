"""LLM-powered musician agents that use Claude for improvisational decisions."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from virtual_band.agents import (
    MusicianAgent, Drummer, Bassist, Pianist, Vocalist,
    PerceivedState, CHORD_ROOTS, PENTATONIC_INTERVALS,
)
from virtual_band.band_state import BandState
from virtual_band.config import LLMConfig
from virtual_band.events import EventBus, EventType, MusicalEvent
from virtual_band.prompts import AGENT_PROMPTS, DECISION_SCHEMA, format_band_context

logger = logging.getLogger(__name__)


class LLMAgent(MusicianAgent):
    """Hybrid agent: uses LLM for high-level decisions, rule-based for note rendering.

    Calls the LLM every `decision_interval` ticks for musical direction,
    then uses the underlying rule-based agent for tick-by-tick note generation
    with the LLM's guidance applied as modifiers.
    """

    def __init__(
        self,
        name: str,
        bus: EventBus,
        fallback: MusicianAgent,
        config: LLMConfig,
        band_state: BandState | None = None,
    ) -> None:
        super().__init__(name, bus, band_state)
        self._fallback = fallback
        self._config = config
        self._client: Any = None
        self._last_decision: dict | None = None
        self._decision_tick: int = -1
        self._system_prompt = AGENT_PROMPTS.get(name, AGENT_PROMPTS["Pianist"])

    def _get_client(self) -> Any:
        """Lazy-init the Anthropic client."""
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self._config.api_key)
            except ImportError:
                logger.warning("anthropic package not installed, LLM agent will use fallback")
                return None
        return self._client

    async def _query_llm(self, tick: int) -> dict | None:
        """Ask Claude for a musical decision."""
        client = self._get_client()
        if not client or not self._config.api_key:
            return None

        context = format_band_context(self.name, self.state, self.band, tick)
        user_msg = (
            f"{context}\n\n"
            f"What should {self.name} play on this beat? "
            f"Respond with JSON: {{\"action\": \"note\"|\"notes\"|\"rest\", "
            f"\"pitches\": [midi_numbers], \"velocity\": 20-127, \"duration\": 1-8}}"
        )

        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model=self._config.model,
                    max_tokens=self._config.max_tokens,
                    system=self._system_prompt,
                    messages=[{"role": "user", "content": user_msg}],
                ),
                timeout=self._config.timeout_seconds,
            )
            text = response.content[0].text.strip()
            # Extract JSON from response
            if "{" in text:
                json_str = text[text.index("{"):text.rindex("}") + 1]
                return json.loads(json_str)
        except asyncio.TimeoutError:
            logger.debug("LLM timeout for %s at tick %d", self.name, tick)
        except Exception as exc:
            logger.debug("LLM error for %s: %s", self.name, exc)
        return None

    async def react(self, event: MusicalEvent) -> None:
        """Delegate reaction to the fallback agent."""
        await self._fallback.react(event)

    async def play_tick(self, tick: int) -> list[MusicalEvent]:
        """Hybrid: LLM decides every N ticks, fallback fills in between."""
        # Query LLM at the decision interval
        if tick % self._config.decision_interval == 0:
            decision = await self._query_llm(tick)
            if decision:
                self._last_decision = decision
                self._decision_tick = tick

        # If we have a recent LLM decision, try to use it
        if self._last_decision and (tick - self._decision_tick) < self._config.decision_interval:
            events = self._render_decision(tick, self._last_decision)
            if events:
                for e in events:
                    await self.emit(e)
                return events

        # Fallback to rule-based
        return await self._fallback.play_tick(tick)

    def _render_decision(self, tick: int, decision: dict) -> list[MusicalEvent]:
        """Convert an LLM decision dict into MusicalEvents."""
        action = decision.get("action", "rest")
        if action == "rest":
            return []

        pitches = decision.get("pitches", [])
        velocity = max(20, min(127, decision.get("velocity", self.state.intensity)))
        duration = max(1, min(8, decision.get("duration", 1)))

        if not pitches:
            return []

        events = []
        for pitch in pitches:
            if 0 <= pitch <= 127:
                events.append(self._make_event(
                    tick=tick,
                    event_type=EventType.NOTE_ON,
                    pitch=pitch,
                    velocity=velocity,
                    duration=duration,
                ))
        return events


def create_llm_agents(
    bus: EventBus,
    config: LLMConfig,
    band_state: BandState | None = None,
) -> list[MusicianAgent]:
    """Create the four band agents with LLM wrappers over rule-based fallbacks."""
    fallbacks = {
        "Drummer": Drummer(bus, band_state=band_state),
        "Bassist": Bassist(bus, band_state=band_state),
        "Pianist": Pianist(bus, band_state=band_state),
        "Vocalist": Vocalist(bus, band_state=band_state),
    }

    agents = []
    for name, fallback in fallbacks.items():
        agent = LLMAgent(
            name=name,
            bus=bus,
            fallback=fallback,
            config=config,
            band_state=band_state,
        )
        agents.append(agent)
    return agents
