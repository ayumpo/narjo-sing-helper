import pytest
import sqlite3

from narjo_sing.config import Settings
from narjo_sing.jobs import JobStore
from narjo_sing.quality import queued_model
from narjo_sing.tiers import TierPlan

SETTINGS = Settings.from_env({})
TWO_TIER = TierPlan("two-tier", "htdemucs", "melband_kim", True, False)


@pytest.fixture
def store(tmp_path):
    ticks = iter(range(1, 10_000))
    return JobStore(tmp_path / "jobs.sqlite", clock=lambda: float(next(ticks)))


def test_duplicate_requests_share_a_job_and_raise_priority(store):
    batch = store.submit("k", "x.flac", 200, "batch", "htdemucs", background=True)
    assert store.submit("k", "x.flac", 200, "batch", "htdemucs", background=True).id == batch.id
    now = store.submit("k", "x.flac", 200, "now", "htdemucs", background=False)
    assert now.id == batch.id and now.priority == "now" and now.background is False
    other_model = store.submit("k", "x.flac", 200, "upgrade", "melband_kim", background=True)
    assert other_model.id != batch.id


def test_queue_order_and_background_gate(store):
    store.submit("u", "u", 1, "upgrade", "melband_kim", True)
    store.submit("b", "b", 1, "batch", "melband_kim", True)
    store.submit("n", "n", 1, "next", "htdemucs", False)
    store.submit("w", "w", 1, "now", "htdemucs", False)
    order = []
    while (job := store.next_runnable(allow_background=True)) is not None:
        order.append(job.key)
        store.mark_running(job.id)
        store.finish(job.id)
    assert order == ["w", "n", "b", "u"]
    store.submit("u2", "u2", 1, "upgrade", "melband_kim", True)
    assert store.next_runnable(allow_background=False) is None


def test_urgent_detection_requeue_and_recover(store):
    job = store.submit("u", "u", 1, "upgrade", "melband_kim", True)
    store.mark_running(job.id)
    assert not store.has_urgent_queued()
    store.submit("w", "w", 1, "now", "htdemucs", False)
    assert store.has_urgent_queued()
    store.requeue(job.id)
    assert store.get(job.id).state == "queued"
    store.mark_running(job.id)
    store.update_progress(job.id, 0.5, 30.0)
    assert store.recover() == 1
    assert store.get(job.id).state == "queued" and store.get(job.id).eta is None


def test_has_other_now_queued_detects_a_different_songs_request(store):
    job = store.submit("a", "a", 1, "now", "melband_kim", False)
    store.mark_running(job.id)
    assert store.has_other_now_queued("a") is False
    store.submit("b", "b", 1, "now", "htdemucs", False)
    assert store.has_other_now_queued("a") is True


def test_has_other_now_queued_ignores_a_queued_job_for_the_same_song(store):
    job = store.submit("a", "a", 1, "now", "melband_kim", False)
    store.mark_running(job.id)
    store.submit("a", "a", 1, "now", "htdemucs", False)  # a different model, so a second job rather than a merge
    assert store.has_other_now_queued("a") is False


def test_set_aside_requeues_a_running_now_job_and_demotes_it_to_next(store):
    job = store.submit("a", "a", 1, "now", "melband_kim", False)
    store.mark_running(job.id)
    store.update_progress(job.id, 0.5, 30.0)
    store.set_aside(job.id)
    updated = store.get(job.id)
    assert (updated.state, updated.priority, updated.progress, updated.eta) == ("queued", "next", 0.0, None)


def test_set_aside_leaves_a_non_now_priority_alone(store):
    job = store.submit("a", "a", 1, "batch", "melband_kim", True)
    store.mark_running(job.id)
    store.set_aside(job.id)
    assert (store.get(job.id).state, store.get(job.id).priority) == ("queued", "batch")


def test_set_aside_only_affects_a_running_job(store):
    queued = store.submit("a", "a", 1, "now", "melband_kim", False)
    store.set_aside(queued.id)
    assert (store.get(queued.id).state, store.get(queued.id).priority) == ("queued", "now")


def test_progress_finish_fail(store):
    job = store.submit("k", "x", 100, "now", "htdemucs", False)
    store.mark_running(job.id)
    store.update_progress(job.id, 0.5, 30.0)
    assert (store.get(job.id).progress, store.get(job.id).eta) == (0.5, 30.0)
    store.finish(job.id)
    assert (store.get(job.id).state, store.get(job.id).progress) == ("done", 1.0)
    failed = store.submit("k2", "y", 100, "now", "htdemucs", False)
    store.fail(failed.id, "boom")
    assert (store.get(failed.id).state, store.get(failed.id).error) == ("failed", "boom")


def test_upgrade_pending_usage_and_active_keys(store):
    store.submit("k", "x", 1, "upgrade", "melband_kim", True)
    assert store.upgrade_pending("k") and not store.upgrade_pending("other")
    assert store.active_keys() == {"k"} and store.queue_length() == 1
    store.touch("k")
    assert set(store.usage()) == {"k"}
    done = store.record_done("d", "z", 5, "now", "htdemucs")
    assert done.state == "done" and store.queue_length() == 1


def test_the_newest_now_request_runs_first(store):
    first = store.submit("a", "a", 1, "now", "htdemucs", False)
    store.submit("n", "n", 1, "next", "htdemucs", False)
    store.submit("b", "b", 1, "now", "htdemucs", False)
    assert store.get(first.id).priority == "next"
    order = []
    while (job := store.next_runnable(allow_background=True)) is not None:
        order.append(job.key)
        store.mark_running(job.id)
        store.finish(job.id)
    assert order == ["b", "a", "n"]


def test_asking_again_for_an_earlier_song_makes_it_current(store):
    store.submit("a", "a", 1, "now", "htdemucs", False)
    store.submit("b", "b", 1, "now", "htdemucs", False)
    again = store.submit("a", "a", 1, "now", "htdemucs", False)
    assert again.priority == "now"
    assert store.next_runnable(allow_background=True).key == "a"


def test_queued_jobs_follow_the_configured_models(store):
    from narjo_sing.tiers import TierPlan
    plan = TierPlan("two-tier", "htdemucs", "melband_kim", True, False)
    old_upgrade = store.submit("a", "a", 1, "upgrade", "retired_model", True)
    old_batch = store.submit("b", "b", 1, "batch", "retired_model", True)
    current = store.submit("c", "c", 1, "now", "htdemucs", False)
    store.submit("d", "d", 1, "upgrade", "melband_kim", True)
    duplicate = store.submit("d", "d", 1, "upgrade", "retired_model", True)
    running = store.submit("e", "e", 1, "upgrade", "retired_model", True)
    store.mark_running(running.id)
    assert store.retarget_queued(lambda job: queued_model(job, plan, SETTINGS, True)) == 3
    assert store.get(old_upgrade.id).model == "melband_kim"
    assert store.get(old_batch.id).model == "melband_kim"
    assert store.get(current.id).model == "htdemucs"
    assert store.get(duplicate.id) is None
    assert store.get(running.id).model == "retired_model"


def test_queued_upgrades_are_dropped_when_upgrades_are_off(store):
    from narjo_sing.tiers import TierPlan
    plan = TierPlan("fast-only", "htdemucs", None, False, True)
    upgrade = store.submit("a", "a", 1, "upgrade", "retired_model", True)
    batch = store.submit("b", "b", 1, "batch", "retired_model", True)
    requested = store.submit("c", "c", 1, "upgrade", "melband_kim", True, requested_quality="both")
    assert store.retarget_queued(lambda job: queued_model(job, plan, SETTINGS, True)) == 2
    assert store.get(upgrade.id) is None
    assert store.get(batch.id).model == "htdemucs"
    assert store.get(requested.id).model == "melband_kim"


def test_jobs_remember_the_requested_quality_and_an_explicit_choice_wins_a_merge(store):
    auto = store.submit("k", "x", 1, "upgrade", "melband_kim", True)
    assert auto.requested_quality == "auto"
    merged = store.submit("k", "x", 1, "upgrade", "melband_kim", True, requested_quality="both")
    assert merged.id == auto.id and merged.requested_quality == "both"
    again = store.submit("k", "x", 1, "upgrade", "melband_kim", True)
    assert again.requested_quality == "both"
    assert store.record_done("d", "z", 5, "now", "htdemucs", requested_quality="fast").requested_quality == "fast"


def test_a_fast_merge_never_overrides_automatic_but_automatic_overrides_fast(store):
    auto = store.submit("k", "x", 1, "now", "htdemucs", False)
    merged = store.submit("k", "x", 1, "now", "htdemucs", False, requested_quality="fast")
    assert merged.id == auto.id and merged.requested_quality == "auto"

    fast = store.submit("j", "y", 1, "now", "htdemucs", False, requested_quality="fast")
    promoted = store.submit("j", "y", 1, "now", "htdemucs", False)
    assert promoted.id == fast.id and promoted.requested_quality == "auto"


def test_an_automatic_batch_jobs_both_choice_survives_a_merge_and_a_restart(store):
    auto = store.submit("k", "x", 1, "batch", "melband_kim", True)
    merged = store.submit("k", "x", 1, "upgrade", "melband_kim", True, requested_quality="both")
    assert merged.id == auto.id and merged.priority == "batch" and merged.requested_quality == "both"
    store.retarget_queued(lambda job: queued_model(job, TWO_TIER, SETTINGS, True))
    assert store.get(auto.id).model == "melband_kim"


def test_a_both_upgrade_choice_survives_a_promotion_to_now_and_a_restart(store):
    upgrade = store.submit("k", "x", 1, "upgrade", "melband_kim", True, requested_quality="both")
    promoted = store.submit("k", "x", 1, "now", "melband_kim", False, requested_quality="best")
    assert promoted.id == upgrade.id and promoted.priority == "now" and promoted.requested_quality == "both"
    store.retarget_queued(lambda job: queued_model(job, TWO_TIER, SETTINGS, True))
    assert store.get(upgrade.id).model == "melband_kim"


def test_cancel_song_stops_every_job_of_that_song(store):
    fast = store.submit("k", "x", 1, "now", "htdemucs", False, requested_quality="both")
    upgrade = store.submit("k", "x", 1, "upgrade", "melband_kim", True, requested_quality="both")
    other = store.submit("o", "y", 1, "next", "htdemucs", False)
    store.mark_running(fast.id)
    assert store.cancel_song(upgrade.id) == 2
    assert (store.get(fast.id).state, store.get(upgrade.id).state) == ("cancelled", "cancelled")
    assert store.get(other.id).state == "queued"
    assert store.is_cancelled(fast.id) and not store.is_cancelled(other.id)
    assert store.cancel_song("unknown") is None
    assert store.queue_length() == 1 and not store.upgrade_pending("k")


def test_a_cancelled_job_cannot_start_be_set_aside_or_fail(store):
    queued = store.submit("k", "x", 1, "now", "htdemucs", False)
    store.cancel_song(queued.id)
    assert store.mark_running(queued.id) is False
    running = store.submit("r", "y", 1, "upgrade", "melband_kim", True)
    store.mark_running(running.id)
    store.cancel_song(running.id)
    store.requeue(running.id)
    store.set_aside(running.id)
    store.fail(running.id, "late failure")
    assert store.get(running.id).state == "cancelled" and store.get(running.id).error is None
    assert store.next_runnable(allow_background=True) is None


def test_a_new_request_after_a_cancel_creates_a_new_job(store):
    first = store.submit("k", "x", 1, "now", "htdemucs", False)
    store.cancel_song(first.id)
    second = store.submit("k", "x", 1, "now", "htdemucs", False)
    assert second.id != first.id and second.state == "queued"


def test_a_finished_job_cannot_be_cancelled(store):
    job = store.submit("k", "x", 1, "now", "htdemucs", False)
    store.mark_running(job.id)
    store.finish(job.id)
    assert store.cancel_song(job.id) == 0 and store.get(job.id).state == "done"


def test_pause_is_remembered_across_restarts(tmp_path):
    first = JobStore(tmp_path / "jobs.sqlite")
    assert first.paused() is False
    first.set_paused(True)
    assert JobStore(tmp_path / "jobs.sqlite").paused() is True
    first.set_paused(False)
    assert JobStore(tmp_path / "jobs.sqlite").paused() is False


def test_an_older_database_gains_the_requested_quality_column(tmp_path):
    path = tmp_path / "jobs.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, key TEXT NOT NULL, rel_path TEXT NOT NULL, duration REAL NOT NULL, "
               "priority INTEGER NOT NULL, model TEXT NOT NULL, background INTEGER NOT NULL, state TEXT NOT NULL, "
               "progress REAL NOT NULL DEFAULT 0, eta REAL, error TEXT, created REAL NOT NULL, updated REAL NOT NULL)")
    db.execute("INSERT INTO jobs VALUES ('old', 'k', 'x', 1, 0, 'htdemucs', 0, 'queued', 0, NULL, NULL, 1, 1)")
    db.commit()
    db.close()
    store = JobStore(path)
    assert store.get("old").requested_quality == "auto"
    assert store.submit("n", "y", 1, "now", "htdemucs", False, requested_quality="best").requested_quality == "best"
