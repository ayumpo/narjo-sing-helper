from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

UPGRADE_CHOICES = ("auto", "on", "off")


@dataclass(frozen=True)
class Settings:
    music_dir: Path
    models_dir: Path
    stems_dir: Path
    port: int = 8765
    stem_cache_gb: float = 50.0
    rescan_minutes: int = 10
    fast_model: str = "htdemucs"
    best_model: str = "bs_roformer"
    best_upgrade: str = "auto"
    best_hours: str | None = None

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        upgrade = e.get("SING_BEST_UPGRADE", "auto").strip().lower()
        if upgrade not in UPGRADE_CHOICES:
            raise ValueError(f"SING_BEST_UPGRADE must be one of {', '.join(UPGRADE_CHOICES)}, got {upgrade!r}")
        return Settings(
            music_dir=Path(e.get("SING_MUSIC_DIR", "/music")),
            models_dir=Path(e.get("SING_MODELS_DIR", "/models")),
            stems_dir=Path(e.get("SING_STEMS_DIR", "/stems")),
            port=int(e.get("SING_PORT", "8765")),
            stem_cache_gb=float(e.get("SING_STEM_CACHE_GB", "50")),
            rescan_minutes=int(e.get("SING_RESCAN_MINUTES", "10")),
            fast_model=e.get("SING_FAST_MODEL", "htdemucs"),
            best_model=e.get("SING_BEST_MODEL", "bs_roformer"),
            best_upgrade=upgrade,
            best_hours=e.get("SING_BEST_HOURS") or None,
        )
