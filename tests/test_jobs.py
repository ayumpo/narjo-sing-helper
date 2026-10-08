import pytest

from narjo_sing.jobs import JobStore


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
    assert store.recover() == 1
    assert store.get(job.id).state == "queued"


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
    assert store.retarget_queued(plan) == 3
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
    assert store.retarget_queued(plan) == 2
    assert store.get(upgrade.id) is None
    assert store.get(batch.id).model == "htdemucs"
