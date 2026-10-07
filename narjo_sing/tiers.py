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
    if best_rt is not None and best_rt <= SINGLE_MAX_RT:
        return TierPlan("single", best_model, best_model, False, False)
    slow_fast = fast_rt > SINGLE_MAX_RT
    if best_upgrade == "off":
        return TierPlan("fast-only", fast_model, None, False, slow_fast)
    if best_upgrade == "on" or (best_rt is not None and best_rt <= UPGRADE_MAX_RT):
        return TierPlan("two-tier", fast_model, best_model, True, slow_fast)
    return TierPlan("fast-only", fast_model, None, False, slow_fast)


def within_hours(window: str | None, now: clock_time) -> bool:
    if not window:
        return True
    start_text, end_text = window.split("-")
    start, end = clock_time.fromisoformat(start_text.strip()), clock_time.fromisoformat(end_text.strip())
    if start <= end:
        return start <= now < end
    return now >= start or now < end
