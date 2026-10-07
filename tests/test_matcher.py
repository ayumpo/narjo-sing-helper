import pytest
from conftest import make_flac

from narjo_sing.library.index import LibraryIndex
from narjo_sing.library.matcher import SongQuery, match


@pytest.fixture
def index(music, stems):
    make_flac(music / "JC Negron/El Me Salvo/05 - Ante Ti.flac", seconds=4,
              ALBUMARTIST="JC Negron", ALBUM="El Me Salvo", TITLE="Ante Ti", TRACKNUMBER="5")
    make_flac(music / "BJ Putnam/Mas Y Mas/07 - Ante Ti.flac", seconds=3,
              ALBUMARTIST="BJ Putnam", ALBUM="Mas Y Mas", TITLE="Ante Ti", TRACKNUMBER="7")
    make_flac(music / "A/X/01 - Intro.flac", seconds=3, ALBUMARTIST="A", ALBUM="X", TITLE="Intro", TRACKNUMBER="1")
    make_flac(music / "B/Y/01 - Intro.flac", seconds=3, ALBUMARTIST="B", ALBUM="Y", TITLE="Intro", TRACKNUMBER="1")
    built = LibraryIndex(stems / "index.sqlite", music)
    built.scan()
    return built


def test_real_path_from_another_mount_matches_by_suffix(index):
    result = match(index, SongQuery(title="Ante Ti", duration=4.0,
                                    path="/volume1/data/Media/music/JC Negron/El Me Salvo/05 - Ante Ti.flac"))
    assert result.matched_by == "path"
    assert result.file.rel_path == "JC Negron/El Me Salvo/05 - Ante Ti.flac"


def test_ambiguous_file_name_falls_back_to_tags(index):
    result = match(index, SongQuery(title="Intro", duration=3.0, path="/music/Z/01 - Intro.flac",
                                    album_artist="B", album="Y", track=1))
    assert (result.matched_by, result.file.rel_path) == ("tags", "B/Y/01 - Intro.flac")


def test_path_match_requires_close_duration(index):
    result = match(index, SongQuery(title="Ante Ti", duration=60.0, path="/m/JC Negron/El Me Salvo/05 - Ante Ti.flac",
                                    album_artist="JC Negron", album="El Me Salvo"))
    assert result.file is None
    assert result.searched == ("path", "tags")


def test_tags_ignore_album_artist_when_the_server_reports_another(index):
    result = match(index, SongQuery(title="ante ti", duration=4.5, album_artist="Various Artists", album="El Me Salvo"))
    assert result.matched_by == "tags"
    assert result.file.rel_path.startswith("JC Negron/")


def test_not_found_lists_what_was_searched(index):
    result = match(index, SongQuery(title="Nope", duration=4.0, album="El Me Salvo"))
    assert (result.file, result.matched_by, result.searched) == (None, None, ("tags",))
