from __future__ import annotations

from dataclasses import dataclass

from .index import IndexedFile, LibraryIndex
from .normalize import norm

DURATION_TOLERANCE = 2.0


@dataclass(frozen=True)
class SongQuery:
    title: str
    duration: float
    path: str | None = None
    album_artist: str | None = None
    album: str | None = None
    disc: int | None = None
    track: int | None = None


@dataclass(frozen=True)
class MatchResult:
    file: IndexedFile | None
    matched_by: str | None
    searched: tuple[str, ...]


def match(index: LibraryIndex, query: SongQuery) -> MatchResult:
    searched: list[str] = []
    if query.path:
        searched.append("path")
        parts = [p for p in query.path.replace("\\", "/").split("/") if p]
        for k in range(len(parts), 0, -1):
            hits = index.by_suffix("/".join(parts[-k:]))
            if not hits:
                continue
            only = hits[0]
            if len(hits) == 1 and (only.duration <= 0 or abs(only.duration - query.duration) <= DURATION_TOLERANCE):
                return MatchResult(only, "path", tuple(searched))
            break  # the longest matching suffix is ambiguous or the wrong length; tags decide
    searched.append("tags")
    title, album, artist = norm(query.title), norm(query.album), norm(query.album_artist)
    if title and album:
        for require_artist in ((True, False) if artist else (False,)):
            hits = index.by_tags(artist, album, title, query.disc, query.track, query.duration,
                                 tolerance=DURATION_TOLERANCE, require_artist=require_artist)
            if hits:
                return MatchResult(hits[0], "tags", tuple(searched))
    return MatchResult(None, None, tuple(searched))
