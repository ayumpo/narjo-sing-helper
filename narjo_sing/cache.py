from __future__ import annotations

import shutil
from pathlib import Path


def evict(stems_dir: Path, max_bytes: int, usage: dict[str, float], protect: set[str]) -> list[str]:
    entries = []
    for song_dir in stems_dir.iterdir():
        meta = song_dir / "meta.json"
        if not song_dir.is_dir() or not meta.exists():
            continue
        size = sum(f.stat().st_size for f in song_dir.rglob("*") if f.is_file())
        entries.append((usage.get(song_dir.name, meta.stat().st_mtime), song_dir.name, size))
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
