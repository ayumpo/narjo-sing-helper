"""SQLite index of the mounted music library, refreshed incrementally by size and mtime."""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import mutagen

from .normalize import norm, norm_int

AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".m4a", ".aac", ".alac", ".ogg", ".oga", ".opus",
    ".wav", ".aif", ".aiff", ".wma", ".ape", ".wv", ".dsf",
}
SKIP_DIRS = {"@eaDir", "#recycle", "#snapshot", "lost+found"}
COMMIT_EVERY = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    rel_path TEXT PRIMARY KEY,
    rev_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    album_artist TEXT NOT NULL,
    album TEXT NOT NULL,
    title TEXT NOT NULL,
    disc INTEGER,
    track INTEGER,
    duration REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS files_rev ON files(rev_path);
CREATE INDEX IF NOT EXISTS files_title_album ON files(title, album);
"""


@dataclass(frozen=True)
class IndexedFile:
    rel_path: str
    size: int
    mtime_ns: int
    duration: float


def read_tags(path: Path) -> dict:
    audio = mutagen.File(path, easy=True)
    if audio is None:
        return {"title": norm(path.stem), "duration": 0.0}
    tags = audio.tags or {}

    def first(key: str):
        try:
            values = tags.get(key)
        except (KeyError, ValueError):
            return None
        if not values:
            return None
        return values[0] if isinstance(values, list) else values

    return {
        "album_artist": norm(first("albumartist") or first("artist")),
        "album": norm(first("album")),
        "title": norm(first("title") or path.stem),
        "disc": norm_int(first("discnumber")),
        "track": norm_int(first("tracknumber")),
        "duration": float(getattr(audio.info, "length", 0.0) or 0.0),
    }


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class LibraryIndex:
    def __init__(self, db_path: Path, music_dir: Path):
        self.music_dir = music_dir
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock:
            self._db.executescript(SCHEMA)

    def _walk(self):
        for root, dirs, files in os.walk(self.music_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for name in files:
                if name.startswith(".") or Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                yield Path(root) / name

    def scan(self) -> dict:
        with self._lock:
            known = {r[0]: (r[1], r[2]) for r in self._db.execute("SELECT rel_path, size, mtime_ns FROM files")}
        seen: set[str] = set()
        added = updated = pending = 0
        for path in self._walk():
            rel = path.relative_to(self.music_dir).as_posix()
            try:
                st = path.stat()
            except OSError:
                continue
            seen.add(rel)
            if known.get(rel) == (st.st_size, st.st_mtime_ns):
                continue
            try:
                tags = read_tags(path)
            except Exception:
                tags = {"title": norm(path.stem), "duration": 0.0}
            row = (rel, rel.casefold()[::-1], st.st_size, st.st_mtime_ns,
                   tags.get("album_artist", ""), tags.get("album", ""), tags.get("title", ""),
                   tags.get("disc"), tags.get("track"), tags.get("duration", 0.0))
            with self._lock:
                self._db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?,?)", row)
                pending += 1
                if pending >= COMMIT_EVERY:
                    self._db.commit()
                    pending = 0
            if rel in known:
                updated += 1
            else:
                added += 1
        removed = [rel for rel in known if rel not in seen]
        with self._lock:
            self._db.executemany("DELETE FROM files WHERE rel_path = ?", [(r,) for r in removed])
            self._db.commit()
        return {"added": added, "updated": updated, "removed": len(removed), "total": len(seen)}

    def get(self, rel_path: str) -> IndexedFile | None:
        with self._lock:
            row = self._db.execute(
                "SELECT rel_path, size, mtime_ns, duration FROM files WHERE rel_path = ?", (rel_path,)
            ).fetchone()
        return IndexedFile(*row) if row else None

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def by_suffix(self, suffix: str) -> list[IndexedFile]:
        """Files whose relative path equals `suffix` or ends with "/" + `suffix` (case-insensitive)."""
        rev = suffix.casefold()[::-1]
        with self._lock:
            rows = self._db.execute(
                "SELECT rel_path, size, mtime_ns, duration FROM files "
                "WHERE rev_path = ? OR rev_path LIKE ? ESCAPE '\\'",
                (rev, _like_escape(rev + "/") + "%"),
            ).fetchall()
        return [IndexedFile(*r) for r in rows]

    def by_tags(self, album_artist: str, album: str, title: str, disc: int | None, track: int | None,
                duration: float, tolerance: float = 2.0, require_artist: bool = True) -> list[IndexedFile]:
        sql = ("SELECT rel_path, size, mtime_ns, duration FROM files "
               "WHERE title = ? AND album = ? AND abs(duration - ?) <= ?")
        args: list = [title, album, duration, tolerance]
        if require_artist:
            sql += " AND album_artist = ?"
            args.append(album_artist)
        if disc is not None:
            sql += " AND (disc IS NULL OR disc = ?)"
            args.append(disc)
        if track is not None:
            sql += " AND (track IS NULL OR track = ?)"
            args.append(track)
        sql += " ORDER BY abs(duration - ?), rel_path"
        args.append(duration)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [IndexedFile(*r) for r in rows]
