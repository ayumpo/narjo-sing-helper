from narjo_sing.config import Settings
from narjo_sing.quality import (best_status, best_usable, copy_answers, copy_label, model_for, queued_model,
                                upgrade_wanted)
from narjo_sing.status import ModelTiming
from narjo_sing.tiers import TierPlan

SETTINGS = Settings.from_env({})
TWO_TIER = TierPlan("two-tier", "htdemucs", "melband_kim", True, False)
FAST_ONLY = TierPlan("fast-only", "htdemucs", None, False, True)
SINGLE_BEST = TierPlan("single", "melband_kim", "melband_kim", False, False)


class Queued:
    def __init__(self, priority, requested_quality, model):
        self.priority = priority
        self.requested_quality = requested_quality
        self.model = model


def test_best_status_reads_the_benchmark():
    assert best_status(None) == "pending"
    assert best_status(ModelTiming("melband_kim", 17.0, 11.0)) == "measured"
    assert best_status(ModelTiming("melband_kim", None, 11.0)) == "tooSlow"
    assert best_status(ModelTiming("melband_kim", None, 0.0)) == "unavailable"


def test_automatic_follows_the_tier_plan():
    assert model_for("auto", "now", TWO_TIER, SETTINGS, True) == "htdemucs"
    assert model_for("auto", "batch", TWO_TIER, SETTINGS, True) == "melband_kim"
    assert model_for("auto", "batch", FAST_ONLY, SETTINGS, True) == "htdemucs"
    assert model_for("auto", "now", SINGLE_BEST, SETTINGS, True) == "melband_kim"
    assert model_for("auto", "now", None, SETTINGS, True) == "htdemucs"


def test_explicit_choices_ignore_the_plan():
    for plan in (TWO_TIER, FAST_ONLY, SINGLE_BEST):
        assert model_for("fast", "now", plan, SETTINGS, True) == "htdemucs"
        assert model_for("best", "now", plan, SETTINGS, True) == "melband_kim"
        assert model_for("both", "next", plan, SETTINGS, True) == "htdemucs"
        assert model_for("both", "upgrade", plan, SETTINGS, True) == "melband_kim"


def test_an_unusable_better_model_falls_back_to_fast():
    assert model_for("best", "now", TWO_TIER, SETTINGS, False) == "htdemucs"
    assert model_for("auto", "batch", TWO_TIER, SETTINGS, False) == "htdemucs"
    assert upgrade_wanted("both", TWO_TIER, False) is False


def test_upgrades_follow_the_choice():
    assert upgrade_wanted("both", FAST_ONLY, True) is True
    assert upgrade_wanted("auto", TWO_TIER, True) is True
    assert upgrade_wanted("auto", FAST_ONLY, True) is False
    assert upgrade_wanted("auto", None, True) is False
    assert upgrade_wanted("fast", TWO_TIER, True) is False
    assert upgrade_wanted("best", TWO_TIER, True) is False


def test_which_existing_copy_answers_a_request():
    assert copy_answers("fast", "fast", "now", TWO_TIER, True) is True
    assert copy_answers("fast", "both", "now", TWO_TIER, True) is True
    assert copy_answers("fast", "best", "now", TWO_TIER, True) is False
    assert copy_answers("best", "best", "now", TWO_TIER, True) is True
    assert copy_answers("fast", "auto", "now", TWO_TIER, True) is True
    assert copy_answers("fast", "auto", "batch", TWO_TIER, True) is False
    assert copy_answers("fast", "auto", "batch", FAST_ONLY, True) is True
    assert copy_answers("fast", "best", "now", TWO_TIER, False) is True


def test_queued_jobs_keep_the_listeners_choice():
    assert queued_model(Queued("upgrade", "both", "melband_kim"), FAST_ONLY, SETTINGS, True) == "melband_kim"
    assert queued_model(Queued("upgrade", "auto", "melband_kim"), FAST_ONLY, SETTINGS, True) is None
    assert queued_model(Queued("upgrade", "both", "melband_kim"), TWO_TIER, SETTINGS, False) is None
    assert queued_model(Queued("now", "best", "melband_kim"), FAST_ONLY, SETTINGS, True) == "melband_kim"
    assert queued_model(Queued("batch", "auto", "melband_kim"), TWO_TIER, SETTINGS, True) == "melband_kim"
    # Both keeps the model it was queued with; it is never recomputed from the plan.
    assert queued_model(Queued("batch", "both", "melband_kim"), TWO_TIER, SETTINGS, True) == "melband_kim"
    # Best is recomputed instead, from a fast fallback, a retired model name, or a benchmark that works again.
    assert queued_model(Queued("now", "best", "melband_kim"), TWO_TIER, SETTINGS, False) == "htdemucs"
    assert queued_model(Queued("now", "best", "retired_model"), TWO_TIER, SETTINGS, True) == "melband_kim"
    assert queued_model(Queued("now", "best", "htdemucs"), TWO_TIER, SETTINGS, True) == "melband_kim"


def test_best_usable_reads_the_configured_models_status():
    assert best_usable({"melband_kim": ModelTiming("melband_kim", 17.0, 11.0)}, SETTINGS) is True
    assert best_usable({"melband_kim": ModelTiming("melband_kim", None, 0.0)}, SETTINGS) is False
    assert best_usable({}, SETTINGS) is True


def test_copy_label_follows_the_configured_better_model():
    assert copy_label("melband_kim", SETTINGS) == "best"
    assert copy_label("htdemucs", SETTINGS) == "fast"
