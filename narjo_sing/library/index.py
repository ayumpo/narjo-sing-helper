"""SQLite index of the mounted music library, refreshed incrementally by size and mtime."""

from __future__ import annotations

import logging
import math
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import mutagen

from .normalize import norm, norm_int

log = logging.getLogger(__name__)

MAX_TAG_NUMBER = 9999

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


def _clamped_tag_number(value: int | None) -> int | None:
    """A disc/track tag outside 0..9999 (e.g. a corrupt 20-digit value) can't fit SQLite INTEGER."""
    return value if value is not None and 0 <= value <= MAX_TAG_NUMBER else None


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
        "disc": _clamped_tag_number(norm_int(first("discnumber"))),
        "track": _clamped_tag_number(norm_int(first("tracknumber"))),
        "duration": float(getattr(audio.info, "length", 0.0) or 0.0),
    }


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class LibraryIndex:
    def __init__(self, db_path: Path, music_dir: Path):
        self.music_dir = music_dir
        # True while a scan walks the folder; the status page says so, because a slow share can take minutes.
        self.scanning = False
        # Indexed files the last scan didn't find outside folders it couldn't read. A file is dropped only when the
        # next scan misses it too: a share that hiccups can hide songs, and re-adding them re-reads their tags.
        self._missing_last_scan: set[str] = set()
        # Every indexed file the last scan didn't find; matching prefers the others (a moved song's old row).
        self._unconfirmed: frozenset[str] = frozenset()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock:
            self._db.executescript(SCHEMA)

    def _walk(self, onerror=None):
        for root, dirs, files in os.walk(self.music_dir, onerror=onerror):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for name in files:
                if name.startswith(".") or Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                yield Path(root) / name

    def scan(self) -> dict:
        self.scanning = True
        try:
            return self._scan()
        finally:
            self.scanning = False

    def _scan(self) -> dict:
        with self._lock:
            known = {r[0]: (r[1], r[2]) for r in self._db.execute("SELECT rel_path, size, mtime_ns FROM files")}
        seen: set[str] = set()
        added = updated = pending = 0
        unreadable: list[str] = []  # folders the walk couldn't read, relative to the music folder ("" = all)

        def on_walk_error(exc: OSError) -> None:
            unreadable.append(self._relative_folder(exc.filename))
            log.warning("Library scan: cannot read a directory: %s", exc)

        for path in self._walk(onerror=on_walk_error):
            try:
                rel = path.relative_to(self.music_dir).as_posix()
                seen.add(rel)  # listed, so it exists even if its details can't be read right now
                try:
                    st = path.stat()
                except FileNotFoundError:
                    if path.is_symlink():
                        seen.discard(rel)  # a link to a file that is gone
                    raise
                if known.get(rel) == (st.st_size, st.st_mtime_ns):
                    continue
                try:
                    tags = read_tags(path)
                except Exception:
                    tags = {"title": norm(path.stem), "duration": 0.0}
                duration = tags.get("duration", 0.0)
                if not math.isfinite(duration) or duration < 0:
                    log.warning("Skipping %r: non-finite or negative duration", str(path))
                    continue
                row = (rel, rel.casefold()[::-1], st.st_size, st.st_mtime_ns,
                       tags.get("album_artist", ""), tags.get("album", ""), tags.get("title", ""),
                       tags.get("disc"), tags.get("track"), duration)
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
            except Exception:
                log.warning("Skipping %r after an indexing error", str(path), exc_info=True)
        if not seen and known:
            log.error("Music folder looks empty or unmounted; keeping %d indexed files", len(known))
            return {"added": 0, "updated": 0, "removed": 0, "total": len(known)}
        missing = {rel for rel in known if rel not in seen}
        # Songs in a folder the walk couldn't read aren't known to be gone.
        kept = {rel for rel in missing if self._under(rel, unreadable)}
        if kept:
            log.warning("Library scan: %d folder(s) couldn't be read; keeping %d songs in them",
                        len(set(unreadable)), len(kept))
        confirmable = missing - kept
        removed = sorted(confirmable & self._missing_last_scan)
        self._missing_last_scan = confirmable - set(removed)
        self._unconfirmed = frozenset(missing - set(removed))
        with self._lock:
            self._db.executemany("DELETE FROM files WHERE rel_path = ?", [(r,) for r in removed])
            self._db.commit()
        return {"added": added, "updated": updated, "removed": len(removed), "total": len(seen)}

    def _relative_folder(self, filename) -> str:
        """`filename` relative to the music folder; "" (all of it) for the folder itself or a path outside it."""
        if not filename:
            return ""
        try:
            rel = Path(filename).relative_to(self.music_dir).as_posix()
        except ValueError:
            return ""
        return "" if rel == "." else rel

    @staticmethod
    def _under(rel: str, folders: list[str]) -> bool:
        return any(folder == "" or rel == folder or rel.startswith(folder + "/") for folder in folders)

    def prefer_present(self, files: list[IndexedFile]) -> list[IndexedFile]:
        """The files the last scan found, when there are any among `files`."""
        unconfirmed = self._unconfirmed
        present = [f for f in files if f.rel_path not in unconfirmed]
        return present or files

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
