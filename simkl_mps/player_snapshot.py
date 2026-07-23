"""Immutable player state captured once per monitor cycle."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlayerSnapshot:
    process_name: str
    filepath: str | None
    position_seconds: float | None
    duration_seconds: float | None
    captured_at: float
    playback_state: str | None = None
    confidence: str = "observed"
    error: str | None = None
