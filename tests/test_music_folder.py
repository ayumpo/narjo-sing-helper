"""The helper says plainly when it can't see the music folder, and keeps queued songs until it can.

With the helper on a Mac (Docker Desktop) and the music on a NAS mounted through Finder, a dropped connection
left /music in the container no longer a folder. The index keeps its songs (by design), so requests still
matched, and each job failed with ffmpeg's "Could not decode … Not a directory".
"""
import os
import threading
import time
from datetime import time as clock_time

import pytest
from conftest import FakeRunner, make_flac
from fastapi.testclient import TestClient

from narjo_sing.__main__ import SCAN_PAUSE_FACTOR, music_watch_loop, scan_once, scan_wait_seconds
from narjo_sing.api import AppContext, create_app
from narjo_sing.config import Settings
from narjo_sing.jobs import JobStore
from narjo_sing.library.index import LibraryIndex
from narjo_sing.music_folder import MUSIC_FOLDER_EMPTY, MUSIC_FOLDER_GONE, check_music_folder, music_folder_problem
from narjo_sing.status import HelperStatus, ModelTiming
from narjo_sing.stems import song_key
from narjo_sing.tiers import TierPlan
from narjo_sing.worker import Worker

KEY = "test-key"
TWO_TIER = TierPlan("two-tier", "htdemucs", "melband_kim", True, False)
TIMINGS = {"htdemucs": ModelTiming("htdemucs", 0.5, 2.0), "melband_kim": ModelTiming("melband_kim", 17.0, 11.0)}
SONG = {"clientSongId": "s1", "title": "Pez", "durationSeconds": 2.0, "album": "Re",
        "albumArtist": "Café Tacvba", "path": "/music/Café Tacvba/Re/12 Pez.flac"}


def unmount(music):
    """What a dropped NAS connection looked like in the container: the folder's path is no longer a folder."""
    saved = music.with_name("music-away")
    music.rename(saved)
    music.write_text("")
    return saved


def remount(music, saved):
    music.unlink()
    saved.rename(music)


@pytest.fixture
def library(music, stems):
    make_flac(music / "Café Tacvba/Re/12 Pez.flac", seconds=2, ALBUMARTIST="Café Tacvba", ALBUM="Re", TITLE="Pez")
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    store = JobStore(stems / "jobs.sqlite")
    settings = Settings.from_env({"SING_MUSIC_DIR": str(music), "SING_STEMS_DIR": str(stems)})
    status = HelperStatus(device="cpu: test", plan=TWO_TIER, timings=dict(TIMINGS), benchmarking=False)
    return index, store, settings, status


# --- the check -------------------------------------------------------------------------------------------

def test_a_folder_with_music_has_no_problem(music):
    (music / "song.flac").write_bytes(b"x")
    assert music_folder_problem(music, lambda: 1) is None


def test_a_path_that_is_not_a_folder_is_gone(music):
    unmount(music)
    assert music_folder_problem(music, lambda: 1) == MUSIC_FOLDER_GONE


def test_a_missing_path_is_gone(tmp_path):
    assert music_folder_problem(tmp_path / "nowhere", lambda: 0) == MUSIC_FOLDER_GONE


def test_an_empty_folder_is_a_problem_only_after_songs_were_indexed(music):
    assert music_folder_problem(music, lambda: 0) is None
    assert music_folder_problem(music, lambda: 12) == MUSIC_FOLDER_EMPTY


def test_the_index_is_counted_only_for_an_empty_folder(music):
    (music / "song.flac").write_bytes(b"x")
    assert music_folder_problem(music, lambda: pytest.fail("counted a folder that has files")) is None


def test_the_messages_say_what_to_do():
    for message in (MUSIC_FOLDER_GONE, MUSIC_FOLDER_EMPTY):
        assert "Reconnect it" in message and "restart the helper" in message
        assert len(message) <= 80


def test_check_records_the_problem_and_clears_it(music):
    status = HelperStatus()
    (music / "song.flac").write_bytes(b"x")
    assert check_music_folder(status, music, lambda: 1) is None and status.music_problem is None
    saved = unmount(music)
    assert check_music_folder(status, music, lambda: 1) == MUSIC_FOLDER_GONE
    assert status.music_problem == MUSIC_FOLDER_GONE
    remount(music, saved)
    assert check_music_folder(status, music, lambda: 1) is None and status.music_problem is None


# --- the worker ------------------------------------------------------------------------------------------

def test_queued_songs_wait_while_the_folder_is_gone_and_run_once_it_is_back(library, music):
    index, store, settings, status = library
    file = index.get("Café Tacvba/Re/12 Pez.flac")
    job = store.submit(song_key(file.rel_path, file.size, file.mtime_ns), file.rel_path, file.duration,
                       "now", "htdemucs", False)
    runner = FakeRunner()
    worker = Worker(store, index, runner, settings, status, sleep=lambda _: None, now=lambda: clock_time(12, 0))
    saved = unmount(music)
    assert worker.step() is False
    assert store.get(job.id).state == "queued" and runner.started == []
    assert status.music_problem == MUSIC_FOLDER_GONE
    remount(music, saved)
    assert worker.step() is True
    assert store.get(job.id).state == "done" and status.music_problem is None


def test_a_song_whose_file_is_gone_fails_with_a_plain_message(library, music):
    index, store, settings, status = library
    file = index.get("Café Tacvba/Re/12 Pez.flac")
    job = store.submit(song_key(file.rel_path, file.size, file.mtime_ns), file.rel_path, file.duration,
                       "now", "htdemucs", False)
    (music / file.rel_path).unlink()
    (music / "other.flac").write_bytes(b"x")  # the folder itself is fine
    worker = Worker(store, index, FakeRunner(), settings, status, sleep=lambda _: None, now=lambda: clock_time(12, 0))
    assert worker.step() is True
    failed = store.get(job.id)
    assert failed.state == "failed"
    assert "no longer in your music folder" in failed.error and "decode" not in failed.error.lower()


# --- the API ---------------------------------------------------------------------------------------------

def client_for(library):
    index, store, settings, status = library
    client = TestClient(create_app(AppContext(settings, KEY, index, store, status, "0.2.3")))
    client.headers["X-Narjo-Sing-Key"] = KEY
    return client


def watch_tick(library, music):
    """One pass of music_watch_loop: the API answers from what it found."""
    index, _, _, status = library
    check_music_folder(status, music, index.count)


def test_new_work_is_refused_with_the_reason_while_the_folder_is_gone(library, music):
    client = client_for(library)
    saved = unmount(music)
    watch_tick(library, music)
    job = client.post("/v1/jobs", json={**SONG, "priority": "now"})
    assert job.status_code == 503 and job.json()["detail"] == MUSIC_FOLDER_GONE
    batch = client.post("/v1/jobs/batch", json={"songs": [SONG]})
    assert batch.status_code == 503 and batch.json()["detail"] == MUSIC_FOLDER_GONE
    remount(music, saved)
    watch_tick(library, music)
    assert client.post("/v1/jobs", json={**SONG, "priority": "now"}).status_code == 200


def test_stems_already_prepared_are_still_handed_out_while_the_folder_is_gone(library, music):
    index, store, settings, status = library
    client = client_for(library)
    first = client.post("/v1/jobs", json={**SONG, "priority": "now"}).json()
    Worker(store, index, FakeRunner(), settings, status, sleep=lambda _: None, now=lambda: clock_time(12, 0)).step()
    assert client.get(f"/v1/jobs/{first['jobId']}").json()["state"] == "done"
    unmount(music)
    watch_tick(library, music)
    again = client.post("/v1/jobs", json={**SONG, "priority": "now"})
    assert again.status_code == 200 and again.json()["state"] == "done"
    lookup = client.post("/v1/stems/lookup", json={"songs": [SONG]}).json()
    assert lookup[0]["ready"] is True


def test_the_api_never_touches_the_music_folder(library, music, monkeypatch):
    client = client_for(library)
    import narjo_sing.music_folder as music_folder
    monkeypatch.setattr(music_folder.os, "scandir", lambda *_: pytest.fail("the API read the music folder"))
    assert client.get("/v1/health").status_code == 200
    assert client.post("/v1/jobs", json={**SONG, "priority": "now"}).status_code == 200


def test_health_reports_the_folder_problem_as_an_error(library, music):
    client = client_for(library)
    assert client.get("/v1/health").json()["musicFolderProblem"] is None
    saved = unmount(music)
    watch_tick(library, music)
    body = client.get("/v1/health").json()
    assert body["state"] == "error" and body["error"] == MUSIC_FOLDER_GONE
    assert body["musicFolderProblem"] == MUSIC_FOLDER_GONE
    remount(music, saved)
    watch_tick(library, music)
    body = client.get("/v1/health").json()
    assert body["state"] == "ready" and body["error"] is None and body["musicFolderProblem"] is None


def test_lookup_still_answers_while_the_folder_is_gone(library, music):
    client = client_for(library)
    unmount(music)
    reply = client.post("/v1/stems/lookup", json={"songs": [SONG]})
    assert reply.status_code == 200 and reply.json()[0]["ready"] is False


# --- the scan --------------------------------------------------------------------------------------------

def test_the_scan_keeps_the_index_and_records_the_problem(library, music):
    index, _, _, status = library
    before = index.count()
    unmount(music)
    scan_once(index, status)
    assert index.count() == before
    assert status.music_problem == MUSIC_FOLDER_GONE


def test_the_watch_loop_keeps_the_status_current(library, music):
    index, _, _, status = library
    stop = threading.Event()
    thread = threading.Thread(target=music_watch_loop, args=(index, status, stop, 0.01), daemon=True)
    thread.start()
    try:
        saved = unmount(music)
        assert wait_for(lambda: status.music_problem == MUSIC_FOLDER_GONE)
        remount(music, saved)
        assert wait_for(lambda: status.music_problem is None)
    finally:
        stop.set()
        thread.join(2)


def wait_for(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any folder")
def test_a_folder_the_helper_cannot_read_fails_that_song_and_the_queue_moves_on(library, music):
    index, store, settings, status = library
    make_flac(music / "Other/Album/01 Song.flac", seconds=2, ALBUMARTIST="Other", ALBUM="Album", TITLE="Song")
    index.scan()
    pez = index.get("Café Tacvba/Re/12 Pez.flac")
    other = index.get("Other/Album/01 Song.flac")
    blocked = store.submit(song_key(pez.rel_path, pez.size, pez.mtime_ns), pez.rel_path, pez.duration,
                           "now", "htdemucs", False)
    next_up = store.submit(song_key(other.rel_path, other.size, other.mtime_ns), other.rel_path, other.duration,
                           "next", "htdemucs", False)
    album = music / "Café Tacvba/Re"
    album.chmod(0)
    try:
        worker = Worker(store, index, FakeRunner(), settings, status, sleep=lambda _: None,
                        now=lambda: clock_time(12, 0))
        assert worker.step() is True
        assert store.get(blocked.id).state == "failed"
        assert worker.step() is True
        assert store.get(next_up.id).state == "done"
    finally:
        album.chmod(0o755)


def test_scans_space_out_on_a_slow_share_within_an_hour():
    assert scan_wait_seconds(10, last_scan_seconds=30) == 600
    assert scan_wait_seconds(10, last_scan_seconds=300) == 300 * SCAN_PAUSE_FACTOR
    assert scan_wait_seconds(10, last_scan_seconds=3 * 3600) == 3600, "a long first index doesn't delay hours"
    assert scan_wait_seconds(120, last_scan_seconds=3 * 3600) == 7200, "a longer chosen interval still applies"
    assert SCAN_PAUSE_FACTOR >= 3


def test_health_says_whether_a_scan_is_running(library, music):
    client = client_for(library)
    index = library[0]
    assert client.get("/v1/health").json()["libraryScanning"] is False
    index.scanning = True
    assert client.get("/v1/health").json()["libraryScanning"] is True
