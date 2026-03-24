"""Song data model and persistence for user-created songs."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from virtual_band.orchestrator import SongStructure

# Valid section names and chords that agents understand
VALID_SECTIONS = {"intro", "verse", "chorus", "bridge", "outro"}

VALID_CHORDS = {
    "C", "Cm", "Cmaj7", "Cm7",
    "D", "Dm", "Dmaj7", "Dm7",
    "E", "Em", "Emaj7", "Em7",
    "F", "Fm", "Fmaj7", "Fm7",
    "G", "Gm", "Gmaj7", "Gm7",
    "A", "Am", "Amaj7", "Am7",
    "B", "Bm", "Bmaj7", "Bm7",
}


@dataclass
class Song:
    """A named, persistable song structure."""

    name: str
    parts: list[dict] = field(default_factory=list)
    author: str = "anonymous"
    created_at: float = field(default_factory=time.time)

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty if valid)."""
        errors: list[str] = []
        if not self.name or not self.name.strip():
            errors.append("Song name is required")
        if not self.parts:
            errors.append("Song must have at least one part")
        for i, part in enumerate(self.parts):
            if part.get("section") not in VALID_SECTIONS:
                errors.append(f"Part {i}: invalid section '{part.get('section')}'")
            if part.get("chord") not in VALID_CHORDS:
                errors.append(f"Part {i}: invalid chord '{part.get('chord')}'")
            dur = part.get("duration", 0)
            if not isinstance(dur, int) or dur < 1 or dur > 64:
                errors.append(f"Part {i}: duration must be 1-64, got {dur}")
            intensity = part.get("intensity", 0)
            if not isinstance(intensity, int) or intensity < 0 or intensity > 127:
                errors.append(f"Part {i}: intensity must be 0-127, got {intensity}")
        return errors

    def to_song_structure(self) -> SongStructure:
        """Convert to the internal SongStructure format."""
        return SongStructure(parts=[
            (p["section"], p["chord"], p["duration"], p["intensity"])
            for p in self.parts
        ])

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Song":
        return Song(
            name=data["name"],
            parts=data.get("parts", []),
            author=data.get("author", "anonymous"),
            created_at=data.get("created_at", time.time()),
        )


class SongStore:
    """Persist songs as JSON files in a directory."""

    def __init__(self, directory: str = "data/songs") -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
        return self._dir / f"{safe_name}.json"

    def save(self, song: Song) -> Path:
        path = self._path(song.name)
        path.write_text(json.dumps(song.to_dict(), indent=2), encoding="utf-8")
        return path

    def load(self, name: str) -> Song | None:
        path = self._path(name)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Song.from_dict(data)

    def list_songs(self) -> list[str]:
        return [p.stem for p in sorted(self._dir.glob("*.json"))]

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if path.exists():
            path.unlink()
            return True
        return False
