import logging
import os

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
