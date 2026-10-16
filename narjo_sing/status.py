from __future__ import annotations

from dataclasses import dataclass, field

from .tiers import TierPlan


@dataclass
class ModelTiming:
    model: str
    rt: float | None
    load_seconds: float


@dataclass
class HelperStatus:
    device: str | None = None
    plan: TierPlan | None = None
    timings: dict[str, ModelTiming] = field(default_factory=dict)
    benchmarking: bool = True
    error: str | None = None
    # Why the music folder couldn't be used at the last check (music_folder.py); the API answers with it and never
    # touches the folder itself, which can block on a dropped network share.
    music_problem: str | None = None

    def expected_seconds(self, model: str, duration: float) -> float:
        timing = self.timings.get(model)
        rt = timing.rt if timing and timing.rt else 1.0
        load = timing.load_seconds if timing else 5.0
        return max(1.0, load + rt * duration)
