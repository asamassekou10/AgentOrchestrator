"""Application configuration with environment variable support."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class AudioConfig:
    """Audio engine defaults."""
    default_tempo: int = 120
    min_tempo: int = 40
    max_tempo: int = 240
    ticks_per_beat: int = 4


@dataclass
class LLMConfig:
    """LLM agent configuration."""
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 256
    decision_interval: int = 4  # call LLM every N ticks
    timeout_seconds: float = 5.0
    enabled: bool = False

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")


@dataclass
class ServerConfig:
    """Web server configuration."""
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    performances_dir: str = "data/performances"
    songs_dir: str = "data/songs"


@dataclass
class BandConfig:
    """Top-level configuration combining all sub-configs."""
    audio: AudioConfig = field(default_factory=AudioConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    @staticmethod
    def from_env() -> "BandConfig":
        """Build config from environment variables."""
        return BandConfig(
            audio=AudioConfig(
                default_tempo=int(os.environ.get("BAND_DEFAULT_TEMPO", "120")),
            ),
            llm=LLMConfig(
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
                model=os.environ.get("BAND_LLM_MODEL", "claude-sonnet-4-20250514"),
                enabled=os.environ.get("BAND_LLM_ENABLED", "").lower() in ("1", "true", "yes"),
                decision_interval=int(os.environ.get("BAND_LLM_INTERVAL", "4")),
            ),
            server=ServerConfig(
                host=os.environ.get("BAND_HOST", "0.0.0.0"),
                port=int(os.environ.get("BAND_PORT", "8000")),
                reload=os.environ.get("VIRTUAL_BAND_RELOAD", "").lower() in ("1", "true", "yes"),
                performances_dir=os.environ.get("BAND_PERFORMANCES_DIR", "data/performances"),
                songs_dir=os.environ.get("BAND_SONGS_DIR", "data/songs"),
            ),
        )
