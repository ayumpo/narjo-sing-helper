from __future__ import annotations

import shutil
from pathlib import Path

RESERVE_BYTES = 2_000_000_000  # headroom so a full disk never also breaks sqlite writes and logs


def _entries(stems_dir: Path) -> list[tuple[float, str, int]]:
    entries = []
    for song_dir in stems_dir.iterdir():
        meta = song_dir / "meta.json"
        if not song_dir.is_dir() or not meta.exists():
            continue
        size = sum(f.stat().st_size for f in song_dir.rglob("*") if f.is_file())
        entries.append((meta.stat().st_mtime, song_dir.name, size))
    return entries


def current_usage_bytes(stems_dir: Path) -> int:
    return sum(size for _, _, size in _entries(stems_dir))


def effective_budget(stems_dir: Path, configured_bytes: int) -> int:
    """Caps the configured cache budget so eviction never lets a full disk wedge the helper."""
    free = shutil.disk_usage(stems_dir).free
    return max(0, min(configured_bytes, current_usage_bytes(stems_dir) + free - RESERVE_BYTES))


def evict(stems_dir: Path, max_bytes: int, usage: dict[str, float], protect: set[str]) -> list[str]:
    entries = [(usage.get(name, mtime), name, size) for mtime, name, size in _entries(stems_dir)]
    total = sum(size for _, _, size in entries)
    removed = []
    for _, key, size in sorted(entries):
        if total <= max_bytes:
            break
        if key in protect:
            continue
        shutil.rmtree(stems_dir / key, ignore_errors=True)
        total -= size
        removed.append(key)
    return removed
