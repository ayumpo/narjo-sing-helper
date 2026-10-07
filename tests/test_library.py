from conftest import make_flac

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
    path = make_flac(music / "A/B/01 - X.flac", TITLE="X", ALBUM="B", ALBUMARTIST="A")
    index = LibraryIndex(stems / "index.sqlite", music)
    assert index.scan() == {"added": 1, "updated": 0, "removed": 0, "total": 1}
    assert index.scan() == {"added": 0, "updated": 0, "removed": 0, "total": 1}
    path.unlink()
    assert index.scan()["removed"] == 1
    assert index.count() == 0


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
