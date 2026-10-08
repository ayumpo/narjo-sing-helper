from __future__ import annotations

from dataclasses import dataclass
from datetime import time as clock_time

SINGLE_MAX_RT = 2.0
UPGRADE_MAX_RT = 30.0


@dataclass(frozen=True)
class TierPlan:
    mode: str  # "single" | "two-tier" | "fast-only"
    now_model: str
    best_model: str | None
    upgrade: bool
    background_prep_recommended: bool

    def model_for(self, priority: str) -> str:
        if priority in ("batch", "upgrade") and self.best_model:
            return self.best_model
        return self.now_model


def decide(fast_model: str, best_model: str, fast_rt: float, best_rt: float | None, best_upgrade: str) -> TierPlan:
    slow_fast = fast_rt > SINGLE_MAX_RT
    if best_model == fast_model:
        # One model for everything (e.g. SING_BEST_MODEL=htdemucs): re-separating with the same model gains nothing.
        return TierPlan("single", fast_model, fast_model, False, slow_fast)
    if best_rt is not None and best_rt <= SINGLE_MAX_RT:
        return TierPlan("single", best_model, best_model, False, False)
    if best_upgrade == "off":
        return TierPlan("fast-only", fast_model, None, False, slow_fast)
    if best_upgrade == "on" or (best_rt is not None and best_rt <= UPGRADE_MAX_RT):
        return TierPlan("two-tier", fast_model, best_model, True, slow_fast)
    return TierPlan("fast-only", fast_model, None, False, slow_fast)


def within_hours(window: tuple[clock_time, clock_time] | None, now: clock_time) -> bool:
    """`window` is pre-validated by Settings.from_env, so this never raises."""
    if window is None:
        return True
    start, end = window
    if start <= end:
        return start <= now < end
    return now >= start or now < end
