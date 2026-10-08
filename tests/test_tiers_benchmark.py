import json
from datetime import time as clock_time

import pytest
from conftest import FakeRunner

from narjo_sing.benchmark import BENCH_SECONDS, load_or_run, time_model
from narjo_sing.config import Settings
from narjo_sing.status import HelperStatus, ModelTiming
from narjo_sing.tiers import decide, within_hours


def test_gpu_speed_uses_best_model_for_everything():
    plan = decide("htdemucs", "bs_roformer", fast_rt=0.05, best_rt=0.3, best_upgrade="auto")
    assert (plan.mode, plan.now_model, plan.best_model, plan.upgrade) == ("single", "bs_roformer", "bs_roformer", False)


def test_i5_speed_uses_two_tiers():
    plan = decide("htdemucs", "bs_roformer", fast_rt=0.45, best_rt=17.0, best_upgrade="auto")
    assert (plan.mode, plan.now_model, plan.best_model, plan.upgrade) == ("two-tier", "htdemucs", "bs_roformer", True)
    assert plan.background_prep_recommended is False
    assert (plan.model_for("now"), plan.model_for("batch"), plan.model_for("upgrade")) == ("htdemucs", "bs_roformer", "bs_roformer")


def test_nas_speed_is_fast_only_and_recommends_background_prep():
    plan = decide("htdemucs", "bs_roformer", fast_rt=7.0, best_rt=None, best_upgrade="auto")
    assert (plan.mode, plan.best_model, plan.upgrade, plan.background_prep_recommended) == ("fast-only", None, False, True)
    assert plan.model_for("batch") == "htdemucs"


def test_owner_can_force_the_upgrade_on_or_off():
    assert decide("htdemucs", "bs_roformer", 7.0, None, "on").mode == "two-tier"
    assert decide("htdemucs", "bs_roformer", 0.4, 17.0, "off").mode == "fast-only"


def test_within_hours():
    assert within_hours(None, clock_time(12, 0))
    assert within_hours((clock_time(1, 0), clock_time(7, 0)), clock_time(3, 0))
    assert not within_hours((clock_time(1, 0), clock_time(7, 0)), clock_time(8, 0))
    assert within_hours((clock_time(22, 0), clock_time(6, 0)), clock_time(23, 30))
    assert not within_hours((clock_time(22, 0), clock_time(6, 0)), clock_time(12, 0))


def test_expected_seconds_uses_timings_and_falls_back():
    status = HelperStatus(timings={"htdemucs": ModelTiming("htdemucs", 0.5, 3.0)})
    assert status.expected_seconds("htdemucs", 200) == 103.0
    assert status.expected_seconds("unknown", 10) == 15.0


def fake_clock():
    state = {"t": 0.0}
    return state, (lambda: state["t"]), (lambda s: state.__setitem__("t", state["t"] + s))


def test_time_model_subtracts_load_time():
    state, clock, sleep = fake_clock()
    runner = FakeRunner(polls_needed=20, load_seconds=2.0)
    timing = time_model(runner, "htdemucs", background=False, sleep=sleep, clock=clock)
    # 19 sleeps of 0.5 s = 9.5 s elapsed, minus 2 s load, over a 9 s clip
    assert abs(timing.rt - (9.5 - 2.0) / BENCH_SECONDS) < 1e-9
    assert runner.prepared == ["htdemucs", "htdemucs"]


def test_time_model_gives_up_past_the_cap():
    state, clock, sleep = fake_clock()
    runner = FakeRunner(polls_needed=10_000, load_seconds=2.0)
    timing = time_model(runner, "bs_roformer", background=True, max_rt=1.0, sleep=sleep, clock=clock)
    assert timing.rt is None
    assert runner.started[0][2].cancelled


def test_best_tier_failure_falls_back_to_fast_only_after_retries(tmp_path):
    settings = Settings.from_env({"SING_STEMS_DIR": str(tmp_path)})
    runner = FakeRunner(fail_prepare_models={"bs_roformer"})
    sleeps = []
    result = load_or_run(tmp_path / "benchmark.json", runner, settings, "0.1.0", sleep=sleeps.append)
    assert result["best"]["rt"] is None
    assert sleeps == [30.0, 120.0]
    plan = decide(settings.fast_model, settings.best_model, result["fast"]["rt"], result["best"]["rt"],
                  settings.best_upgrade)
    assert plan.mode == "fast-only"


def test_fast_tier_failure_is_fatal_after_retries(tmp_path):
    settings = Settings.from_env({"SING_STEMS_DIR": str(tmp_path)})
    runner = FakeRunner(fail_prepare_models={"htdemucs"})
    sleeps = []
    with pytest.raises(RuntimeError, match="htdemucs"):
        load_or_run(tmp_path / "benchmark.json", runner, settings, "0.1.0", sleep=sleeps.append)
    assert sleeps == [30.0, 120.0]


def test_results_are_cached_per_version_and_models(tmp_path):
    settings = Settings.from_env({"SING_STEMS_DIR": str(tmp_path)})
    runner = FakeRunner(polls_needed=1)
    first = load_or_run(tmp_path / "benchmark.json", runner, settings, "0.1.0")
    assert first["device"] == "cpu: fake" and first["fast"]["model"] == "htdemucs"
    assert json.loads((tmp_path / "benchmark.json").read_text()) == first
    runner.started.clear()
    assert load_or_run(tmp_path / "benchmark.json", runner, settings, "0.1.0") == first and runner.started == []
    load_or_run(tmp_path / "benchmark.json", runner, settings, "0.2.0")
    assert len(runner.started) == 2


def test_one_model_for_both_tiers_never_upgrades():
    for fast_rt in (0.4, 7.0):
        plan = decide("htdemucs", "htdemucs", fast_rt=fast_rt, best_rt=fast_rt, best_upgrade="auto")
        assert (plan.mode, plan.now_model, plan.best_model, plan.upgrade) == ("single", "htdemucs", "htdemucs", False)
        assert plan.background_prep_recommended is (fast_rt > 2)


def test_one_model_for_both_tiers_is_benchmarked_and_downloaded_once(tmp_path):
    settings = Settings.from_env({"SING_STEMS_DIR": str(tmp_path), "SING_BEST_MODEL": "htdemucs"})
    runner = FakeRunner(polls_needed=1)
    result = load_or_run(tmp_path / "benchmark.json", runner, settings, "0.1.0")
    assert set(runner.prepared) == {"htdemucs"} and len(runner.started) == 1
    assert result["best"] == result["fast"]
