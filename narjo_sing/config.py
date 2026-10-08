from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import time as clock_time
from pathlib import Path

UPGRADE_CHOICES = ("auto", "on", "off")
_HOURS_SEP = re.compile(r"[-–]")
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_clock(text: str, window: str) -> clock_time:
    m = _CLOCK.match(text.strip())
    if not m:
        raise ValueError(f"SING_BEST_HOURS must look like HH:MM-HH:MM, got {window!r}")
    try:
        return clock_time(int(m.group(1)), int(m.group(2)))
    except ValueError:
        raise ValueError(f"SING_BEST_HOURS must look like HH:MM-HH:MM, got {window!r}") from None


def _parse_best_hours(window: str) -> tuple[clock_time, clock_time]:
    """Accepts H:MM or HH:MM on either side of a hyphen or en dash; endpoints must differ."""
    parts = _HOURS_SEP.split(window.strip(), maxsplit=1)
    if len(parts) != 2:
        raise ValueError(f"SING_BEST_HOURS must look like HH:MM-HH:MM, got {window!r}")
    start, end = (_parse_clock(part, window) for part in parts)
    if start == end:
        raise ValueError(f"SING_BEST_HOURS start and end must differ, got {window!r}")
    return start, end


@dataclass(frozen=True)
class Settings:
    music_dir: Path
    models_dir: Path
    stems_dir: Path
    port: int = 8765
    stem_cache_gb: float = 50.0
    rescan_minutes: int = 10
    fast_model: str = "htdemucs"
    best_model: str = "melband_kim"
    best_upgrade: str = "auto"
    best_hours: tuple[clock_time, clock_time] | None = None
    max_minutes: float = 20.0

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        upgrade = e.get("SING_BEST_UPGRADE", "auto").strip().lower()
        if upgrade not in UPGRADE_CHOICES:
            raise ValueError(f"SING_BEST_UPGRADE must be one of {', '.join(UPGRADE_CHOICES)}, got {upgrade!r}")
        hours_text = e.get("SING_BEST_HOURS") or None
        max_minutes = float(e.get("SING_MAX_MINUTES", "20"))
        if max_minutes <= 0:
            raise ValueError(f"SING_MAX_MINUTES must be > 0, got {max_minutes!r}")
        return Settings(
            music_dir=Path(e.get("SING_MUSIC_DIR", "/music")),
            models_dir=Path(e.get("SING_MODELS_DIR", "/models")),
            stems_dir=Path(e.get("SING_STEMS_DIR", "/stems")),
            port=int(e.get("SING_PORT", "8765")),
            stem_cache_gb=float(e.get("SING_STEM_CACHE_GB", "50")),
            rescan_minutes=int(e.get("SING_RESCAN_MINUTES", "10")),
            fast_model=e.get("SING_FAST_MODEL", "htdemucs"),
            best_model=e.get("SING_BEST_MODEL", "melband_kim"),
            best_upgrade=upgrade,
            best_hours=_parse_best_hours(hours_text) if hours_text else None,
            max_minutes=max_minutes,
        )
