from datetime import time as clock_time

import pytest
from conftest import FakeRunner, make_flac

from narjo_sing.config import Settings
from narjo_sing.jobs import JobStore
from narjo_sing.library.index import LibraryIndex
from narjo_sing.status import HelperStatus, ModelTiming
from narjo_sing.stems import read_meta, song_key
from narjo_sing.tiers import TierPlan
from narjo_sing.worker import Worker

TWO_TIER = TierPlan("two-tier", "htdemucs", "melband_kim", True, False)
FAST_ONLY = TierPlan("fast-only", "htdemucs", None, False, True)


@pytest.fixture
def setup(music, stems):
    make_flac(music / "A/B/01 - Song.flac", seconds=2, ALBUMARTIST="A", ALBUM="B", TITLE="Song")
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    store = JobStore(stems / "jobs.sqlite")
    file = index.get("A/B/01 - Song.flac")
    key = song_key(file.rel_path, file.size, file.mtime_ns)
    settings = Settings.from_env({"SING_MUSIC_DIR": str(music), "SING_STEMS_DIR": str(stems)})

    def worker(runner, plan=TWO_TIER, hours_now=clock_time(12, 0), sleep=lambda _: None, clock=None, timings=None,
               **overrides):
        s = Settings.from_env({"SING_MUSIC_DIR": str(music), "SING_STEMS_DIR": str(stems), **overrides})
        status = HelperStatus(plan=plan, timings=dict(timings or {}))
        extra = {"clock": clock} if clock else {}
        return Worker(store, index, runner, s, status, sleep=sleep, now=lambda: hours_now, **extra)

    return store, file, key, settings, worker


def test_fast_job_writes_stems_then_queues_the_upgrade(setup, stems):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    runner = FakeRunner()
    assert worker(runner).step() is True
    assert store.get(job.id).state == "done"
    assert read_meta(stems, key).quality == "fast"
    assert store.upgrade_pending(key)
    assert worker(runner).step() is True
    assert read_meta(stems, key).quality == "best"
    assert [(m, bg) for m, bg, _ in runner.started] == [("htdemucs", False), ("melband_kim", True)]


def test_urgent_request_preempts_a_running_upgrade(setup):
    store, file, key, _, worker = setup
    upgrade = store.submit(key, file.rel_path, file.duration, "upgrade", "melband_kim", True)

    def on_poll(count):
        if count == 2:
            store.submit("other", "elsewhere.flac", 3, "now", "htdemucs", False)

    runner = FakeRunner(polls_needed=50, on_poll=on_poll)
    assert worker(runner).step() is True
    assert runner.started[0][2].cancelled
    assert store.get(upgrade.id).state == "queued"


def test_changed_file_fails_the_job(setup):
    store, file, _, _, worker = setup
    job = store.submit("stale-key", file.rel_path, file.duration, "now", "htdemucs", False)
    worker(FakeRunner()).step()
    assert store.get(job.id).state == "failed"
    assert "changed or was removed" in store.get(job.id).error


def test_separation_failure_is_recorded(setup):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    worker(FakeRunner(fail="model exploded")).step()
    assert store.get(job.id).state == "failed" and "model exploded" in store.get(job.id).error


def test_best_hours_hold_background_work(setup):
    store, file, key, _, worker = setup
    store.submit(key, file.rel_path, file.duration, "upgrade", "melband_kim", True)
    held = worker(FakeRunner(), hours_now=clock_time(12, 0), SING_BEST_HOURS="01:00-07:00")
    assert held.step() is False
    allowed = worker(FakeRunner(), hours_now=clock_time(3, 0), SING_BEST_HOURS="01:00-07:00")
    assert allowed.step() is True


def test_song_longer_than_max_minutes_fails_without_decoding(setup, monkeypatch):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)

    def must_not_decode(*args, **kwargs):
        raise AssertionError("must not decode a song over SING_MAX_MINUTES")

    monkeypatch.setattr("narjo_sing.worker.decode", must_not_decode)
    held = worker(FakeRunner(), SING_MAX_MINUTES="0.01")
    assert held.step() is True
    failed = store.get(job.id)
    assert failed.state == "failed"
    assert "SING_MAX_MINUTES" in failed.error


def test_no_plan_means_no_work_yet(setup):
    store, file, key, _, worker = setup
    store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    assert worker(FakeRunner(), plan=None).step() is False


def test_save_failure_marks_the_job_failed(setup, monkeypatch):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)

    def fail_write(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("narjo_sing.worker.write_stem_pair", fail_write)
    assert worker(FakeRunner()).step() is True
    assert store.get(job.id).state == "failed"
    assert "disk full" in store.get(job.id).error
    assert not store.upgrade_pending(key)


def test_runner_start_failure_fails_the_job_and_returns_normally(setup):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    runner = FakeRunner(fail_start="gpu OOM")
    assert worker(runner).step() is True
    assert store.get(job.id).state == "failed"
    assert "gpu OOM" in store.get(job.id).error


def test_poll_failure_cancels_the_run_and_fails_the_job(setup):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    runner = FakeRunner(polls_needed=50, poll_fail_after=2)
    assert worker(runner).step() is True
    assert runner.started[0][2].cancelled is True
    assert store.get(job.id).state == "failed"


def test_save_failure_still_runs_eviction(setup, monkeypatch):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    called = []

    def fail_write(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("narjo_sing.worker.write_stem_pair", fail_write)
    monkeypatch.setattr("narjo_sing.worker.evict", lambda *a, **k: called.append(True))
    assert worker(FakeRunner()).step() is True
    assert store.get(job.id).state == "failed"
    assert called == [True]


def test_eviction_failure_does_not_fail_the_job(setup, stems, monkeypatch):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)

    def fail_evict(*args, **kwargs):
        raise OSError("busy")

    monkeypatch.setattr("narjo_sing.worker.evict", fail_evict)
    assert worker(FakeRunner()).step() is True
    assert store.get(job.id).state == "done"
    assert read_meta(stems, key) is not None


def test_bookkeeping_failure_after_finish_keeps_the_job_done(setup, stems, monkeypatch):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)

    def fail_touch(*args, **kwargs):
        raise RuntimeError("database or disk is full")

    monkeypatch.setattr(store, "touch", fail_touch)
    assert worker(FakeRunner()).step() is True
    assert store.get(job.id).state == "done"
    assert read_meta(stems, key) is not None


def test_a_job_interrupted_by_a_restart_moves_to_the_configured_model(setup):
    import threading
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "upgrade", "retired_model", True)
    store.mark_running(job.id)
    stop = threading.Event()
    stop.set()
    worker(FakeRunner()).run_forever(stop)
    assert (store.get(job.id).state, store.get(job.id).model) == ("queued", "melband_kim")


def test_a_paused_helper_starts_nothing(setup):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    store.set_paused(True)
    runner = FakeRunner()
    assert worker(runner).step() is False
    assert store.get(job.id).state == "queued" and runner.started == []
    store.set_paused(False)
    assert worker(runner).step() is True and store.get(job.id).state == "done"


def test_pause_freezes_the_running_song_and_resume_continues_it(setup):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    sleeps = []

    def on_poll(polls):
        if polls == 2:
            store.set_paused(True)

    def sleep(_):
        sleeps.append(1)
        if len(sleeps) == 5:
            store.set_paused(False)

    runner = FakeRunner(polls_needed=4, on_poll=on_poll)
    assert worker(runner, sleep=sleep).step() is True
    run = runner.started[0][2]
    assert (run.suspends, run.resumes, run.cancelled) == (1, 1, False)
    assert store.get(job.id).state == "done"


def test_paused_time_does_not_count_as_progress(setup):
    store, file, key, _, worker = setup
    store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    clock = [0.0]
    sleeps = []
    progress = []

    def on_poll(polls):
        if polls == 2:
            store.set_paused(True)

    def sleep(_):
        clock[0] += 10.0
        sleeps.append(1)
        if len(sleeps) == 4:
            store.set_paused(False)

    record = store.update_progress
    store.update_progress = lambda job_id, value, eta: (progress.append(value), record(job_id, value, eta))
    timings = {"htdemucs": ModelTiming("htdemucs", 50.0, 0.0)}  # 50 s per second of audio: 100 s for this song
    runner = FakeRunner(polls_needed=4, on_poll=on_poll)
    assert worker(runner, sleep=sleep, clock=lambda: clock[0], timings=timings).step() is True
    # Paused from t=10 to t=40; the polls at t=0, 40 and 50 count 0, 10 and 20 seconds of work.
    assert [round(value, 2) for value in progress] == [0.0, 0.1, 0.2]


def test_cancelling_a_queued_song_means_it_never_runs(setup):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    assert store.cancel_song(job.id) == 1
    runner = FakeRunner()
    assert worker(runner).step() is False
    assert store.get(job.id).state == "cancelled" and runner.started == []


def test_cancel_stops_the_running_song_and_keeps_no_copy(setup, stems):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    runner = FakeRunner(polls_needed=5, on_poll=lambda polls: polls == 2 and store.cancel_song(job.id))
    assert worker(runner).step() is True
    assert runner.started[0][2].cancelled is True
    assert store.get(job.id).state == "cancelled" and read_meta(stems, key) is None


def test_cancel_while_paused_ends_the_frozen_song(setup):
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)

    def on_poll(polls):
        if polls == 2:
            store.set_paused(True)
        if polls == 3:
            store.cancel_song(job.id)

    runner = FakeRunner(polls_needed=5, on_poll=on_poll)
    assert worker(runner).step() is True
    run = runner.started[0][2]
    assert run.suspends == 1 and run.cancelled is True
    assert store.get(job.id).state == "cancelled"


def test_a_cancel_that_arrives_once_the_result_exists_keeps_the_copy(setup, stems, monkeypatch):
    import narjo_sing.worker as worker_module
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False)
    real_write = worker_module.write_stem_pair

    def write_then_cancel(*args, **kwargs):
        store.cancel_song(job.id)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(worker_module, "write_stem_pair", write_then_cancel)
    assert worker(FakeRunner()).step() is True
    assert store.get(job.id).state == "done" and read_meta(stems, key) is not None
    assert store.upgrade_pending(key) is False


def test_an_explicit_best_copy_is_labelled_best_on_a_fast_only_server(setup, stems):
    store, file, key, _, worker = setup
    store.submit(key, file.rel_path, file.duration, "now", "melband_kim", False, requested_quality="best")
    assert worker(FakeRunner(), plan=FAST_ONLY).step() is True
    assert read_meta(stems, key).quality == "best"
    assert store.upgrade_pending(key) is False


def test_only_automatic_jobs_queue_the_better_copy_afterwards(setup):
    store, file, key, _, worker = setup
    store.submit(key, file.rel_path, file.duration, "now", "htdemucs", False, requested_quality="fast")
    assert worker(FakeRunner()).step() is True
    assert store.upgrade_pending(key) is False


def test_a_requested_upgrade_survives_a_restart_on_a_fast_only_server(setup):
    import threading
    store, file, key, _, worker = setup
    job = store.submit(key, file.rel_path, file.duration, "upgrade", "melband_kim", True, requested_quality="both")
    stop = threading.Event()
    stop.set()
    worker(FakeRunner(), plan=FAST_ONLY).run_forever(stop)
    assert (store.get(job.id).state, store.get(job.id).model) == ("queued", "melband_kim")
