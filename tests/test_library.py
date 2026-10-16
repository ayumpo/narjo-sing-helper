import logging
import os
from pathlib import Path

from conftest import make_flac

import narjo_sing.library.index as index_module
from narjo_sing.library.index import LibraryIndex
from narjo_sing.library.normalize import norm, norm_int


def test_norm_and_norm_int():
    assert norm("  Wo  sind\tall ") == "wo sind all"
    assert norm(None) == ""
    assert norm("ÁNTE Ti") == "ánte ti"
    assert (norm_int("3/12"), norm_int("03"), norm_int(7), norm_int("x"), norm_int(None)) == (3, 3, 7, None, None)


def test_scan_indexes_audio_and_skips_synology_folders(music, stems):
    make_flac(music / "JC Negron/El Me Salvo/05 - Ante Ti.flac",
              ALBUMARTIST="JC Negron", ALBUM="El Me Salvo", TITLE="Ante Ti", TRACKNUMBER="5", DISCNUMBER="1")
    make_flac(music / "@eaDir/thumb.flac")
    (music / "notes.txt").write_text("not audio")
    index = LibraryIndex(stems / "index.sqlite", music)
    assert index.scan()["total"] == 1
    found = index.get("JC Negron/El Me Salvo/05 - Ante Ti.flac")
    assert found is not None and abs(found.duration - 3.0) < 0.1


def test_rescan_is_incremental_and_drops_removed_files(music, stems):
    # Two files, not one: deleting every file is a separate, protected case (see
    # test_empty_music_dir_keeps_a_populated_index), not what this test exercises.
    path = make_flac(music / "A/B/01 - X.flac", TITLE="X", ALBUM="B", ALBUMARTIST="A")
    make_flac(music / "A/B/02 - Y.flac", TITLE="Y", ALBUM="B", ALBUMARTIST="A")
    index = LibraryIndex(stems / "index.sqlite", music)
    assert index.scan() == {"added": 2, "updated": 0, "removed": 0, "total": 2}
    assert index.scan() == {"added": 0, "updated": 0, "removed": 0, "total": 2}
    path.unlink()
    # A file is dropped once it is missing from two scans in a row (a flaky share can hide it for one).
    assert index.scan()["removed"] == 0 and index.count() == 2
    assert index.scan()["removed"] == 1
    assert index.count() == 1


def test_album_artist_falls_back_to_artist(music, stems):
    make_flac(music / "x.flac", ARTIST="Solo", ALBUM="Al", TITLE="T")
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    assert [f.rel_path for f in index.by_tags("solo", "al", "t", None, None, 3.0)] == ["x.flac"]


def test_suffix_query_is_case_insensitive_and_whole_component(music, stems):
    make_flac(music / "Artist/Album/01 - Song.flac", TITLE="Song")
    make_flac(music / "Artist/Album/101 - Song.flac", TITLE="Song")
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    assert [f.rel_path for f in index.by_suffix("album/01 - song.flac")] == ["Artist/Album/01 - Song.flac"]
    assert [f.rel_path for f in index.by_suffix("01 - Song.flac")] == ["Artist/Album/01 - Song.flac"]


def test_huge_discnumber_is_clamped_instead_of_crashing_the_scan(music, stems):
    make_flac(music / "huge.flac", TITLE="Huge", DISCNUMBER="99999999999999999999")
    index = LibraryIndex(stems / "index.sqlite", music)
    assert index.scan()["total"] == 1
    assert index.get("huge.flac").rel_path == "huge.flac"


def test_bad_row_between_two_good_files_does_not_abort_the_scan(music, stems, monkeypatch):
    make_flac(music / "a.flac", TITLE="First")
    make_flac(music / "bad.flac", TITLE="Bad")
    make_flac(music / "z.flac", TITLE="Last")
    original = index_module.read_tags

    def patched(path):
        tags = original(path)
        if path.name == "bad.flac":
            tags["duration"] = float("nan")
        return tags

    monkeypatch.setattr(index_module, "read_tags", patched)
    index = LibraryIndex(stems / "index.sqlite", music)
    result = index.scan()
    assert result["added"] == 2  # bad.flac is skipped, not indexed; a.flac and z.flac still are
    assert index.get("a.flac") is not None
    assert index.get("z.flac") is not None
    assert index.get("bad.flac") is None


def test_empty_music_dir_keeps_a_populated_index(music, stems):
    path = make_flac(music / "a.flac", TITLE="A")
    index = LibraryIndex(stems / "index.sqlite", music)
    assert index.scan()["total"] == 1
    path.unlink()
    for child in list(music.iterdir()):
        child.unlink()
    result = index.scan()
    assert result["removed"] == 0
    assert index.count() == 1


def test_walk_errors_are_logged_and_scan_continues(music, stems, monkeypatch, caplog):
    make_flac(music / "a.flac", TITLE="A")
    real_walk = os.walk

    def flaky_walk(top, onerror=None, **kwargs):
        if onerror:
            onerror(OSError("permission denied"))
        yield from real_walk(top, onerror=onerror, **kwargs)

    monkeypatch.setattr(index_module.os, "walk", flaky_walk)
    index = LibraryIndex(stems / "index.sqlite", music)
    with caplog.at_level(logging.WARNING):
        result = index.scan()
    assert result["total"] == 1
    assert any("permission denied" in r.message for r in caplog.records)


def unreadable(monkeypatch, music, *names):
    """os.walk failing on these folders the way a dropped or locked share does: onerror with the folder's path."""
    real_walk = os.walk

    def walk(top, onerror=None, **kwargs):
        for root, dirs, files in real_walk(top, onerror=onerror, **kwargs):
            for name in names:
                folder = Path(root) / name
                if name in dirs or folder == music:
                    if name in dirs:
                        dirs.remove(name)
                    onerror(OSError(5, "Input/output error", str(folder)))
            yield root, dirs, files

    monkeypatch.setattr(index_module.os, "walk", walk)


def test_songs_in_a_folder_that_could_not_be_read_are_kept(music, stems, monkeypatch):
    """A share that drops mid-scan fails some folders; their songs were removed, so the index fell toward zero
    and the next scan re-read every file's tags over the slow share."""
    make_flac(music / "A/a.flac", TITLE="A")
    make_flac(music / "B/b.flac", TITLE="B")
    index = LibraryIndex(stems / "index.sqlite", music)
    assert index.scan()["total"] == 2
    unreadable(monkeypatch, music, "B")
    for _ in range(3):
        assert index.scan()["removed"] == 0
    assert index.count() == 2 and index.get("B/b.flac") is not None


def test_a_folder_that_is_never_readable_does_not_stop_removals_elsewhere(music, stems, monkeypatch):
    make_flac(music / "Locked/l.flac", TITLE="L")
    gone = make_flac(music / "A/a.flac", TITLE="A")
    make_flac(music / "A/keep.flac", TITLE="K")
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    unreadable(monkeypatch, music, "Locked")
    gone.unlink()
    assert index.scan()["removed"] == 0
    assert index.scan()["removed"] == 1
    assert index.get("A/a.flac") is None and index.get("Locked/l.flac") is not None


def test_a_song_seen_in_between_starts_counting_again(music, stems, monkeypatch):
    make_flac(music / "A/a.flac", TITLE="A")
    hidden = make_flac(music / "B/b.flac", TITLE="B")
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    away = hidden.with_name("b.away")
    hidden.rename(away)
    assert index.scan()["removed"] == 0            # missed once
    away.rename(hidden)
    with monkeypatch.context() as patch:
        unreadable(patch, music, "A")
        assert index.scan()["removed"] == 0        # seen again, in a scan with an unreadable folder
    hidden.rename(away)
    assert index.scan()["removed"] == 0, "the earlier miss doesn't count after the song was seen"
    assert index.scan()["removed"] == 1


def test_a_moved_song_matches_its_new_path_before_the_old_row_is_dropped(music, stems):
    from narjo_sing.library.matcher import SongQuery, match
    old = make_flac(music / "Incoming/Zappa/Hot Rats/01 Peaches.flac", seconds=3, ALBUMARTIST="Zappa",
                    ALBUM="Hot Rats", TITLE="Peaches")
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    new = music / "Zappa/Hot Rats/01 Peaches.flac"
    new.parent.mkdir(parents=True)
    old.rename(new)
    assert index.scan()["removed"] == 0
    assert index.get("Incoming/Zappa/Hot Rats/01 Peaches.flac") is not None   # kept one more scan
    for query in (SongQuery(title="Peaches", duration=3.0, path="Zappa/Hot Rats/01 Peaches.flac"),
                  SongQuery(title="Peaches", duration=3.0, album="Hot Rats", album_artist="Zappa")):
        assert match(index, query).file.rel_path == "Zappa/Hot Rats/01 Peaches.flac"


def test_a_symlink_to_a_deleted_file_is_dropped(music, stems):
    target = make_flac(music / "real.flac", TITLE="R")
    make_flac(music / "other.flac", TITLE="O")
    (music / "link.flac").symlink_to(target)
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    assert index.get("link.flac") is not None
    target.unlink()
    index.scan()
    index.scan()
    assert index.get("link.flac") is None


def test_a_song_missing_from_one_scan_only_is_kept(music, stems):
    make_flac(music / "a.flac", TITLE="A")
    hidden = make_flac(music / "b.flac", TITLE="B")
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    away = hidden.with_name("b.away")
    hidden.rename(away)
    assert index.scan()["removed"] == 0
    away.rename(hidden)
    assert index.scan()["removed"] == 0
    hidden.rename(away)
    assert index.scan()["removed"] == 0, "missing again, but not twice in a row"
    assert index.scan()["removed"] == 1


def test_a_listed_file_whose_details_cannot_be_read_is_kept(music, stems, monkeypatch):
    make_flac(music / "a.flac", TITLE="A")
    make_flac(music / "b.flac", TITLE="B")
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    real_stat = index_module.Path.stat

    def failing_stat(self, *args, **kwargs):
        if self.name == "b.flac":
            raise OSError("Input/output error")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(index_module.Path, "stat", failing_stat)
    for _ in range(3):
        assert index.scan()["removed"] == 0
    assert index.get("b.flac") is not None


def test_scanning_is_reported_while_a_scan_runs(music, stems, monkeypatch):
    make_flac(music / "a.flac", TITLE="A")
    index = LibraryIndex(stems / "index.sqlite", music)
    seen = []
    original = index_module.read_tags

    def watching(path):
        seen.append(index.scanning)
        return original(path)

    monkeypatch.setattr(index_module, "read_tags", watching)
    assert index.scanning is False
    index.scan()
    assert seen == [True] and index.scanning is False
