"""Which model a job uses: the quality a listener chose in Narjo, read against the server's tier plan."""

from __future__ import annotations

from .config import Settings
from .status import ModelTiming
from .tiers import TierPlan


def best_status(timing: ModelTiming | None) -> str:
    """"pending" while the benchmark runs, "measured", "tooSlow" (stopped at UPGRADE_MAX_RT) or "unavailable".
    `load_or_run` records a failed benchmark as no speed and zero load time; a stopped one keeps its load time."""
    if timing is None:
        return "pending"
    if timing.rt is not None:
        return "measured"
    return "tooSlow" if timing.load_seconds > 0 else "unavailable"


def model_for(quality: str, priority: str, plan: TierPlan | None, settings: Settings, best_usable: bool) -> str:
    """The model a job runs. Priority "upgrade" is the better copy of a song that already has a fast one."""
    if quality == "auto":
        model = plan.model_for(priority) if plan else settings.fast_model
    elif quality == "best" or priority == "upgrade":
        model = settings.best_model
    else:
        model = settings.fast_model
    return model if model != settings.best_model or best_usable else settings.fast_model


def upgrade_wanted(quality: str, plan: TierPlan | None, best_usable: bool) -> bool:
    """Whether a song that has only a fast copy should get the better one in the background."""
    if not best_usable:
        return False
    if quality == "both":
        return True
    return quality == "auto" and plan is not None and plan.upgrade


def copy_answers(copy_quality: str, quality: str, priority: str, plan: TierPlan | None, best_usable: bool) -> bool:
    """Whether a song's existing copy answers a request, so no new job is needed."""
    if copy_quality == "best" or not best_usable or quality in ("fast", "both"):
        return True
    if quality == "best":
        return False
    # Automatic: preparing ahead asks for the better copy when the server makes one.
    return not (priority == "batch" and plan is not None and plan.best_model is not None)


def queued_model(job, plan: TierPlan, settings: Settings, best_usable: bool) -> str | None:
    """The model a queued job should use after a model setting or the benchmark changed; None drops the job.
    Both keeps its already-queued model (falling back to fast once best turns unusable); every other job,
    including Best, is recomputed from `model_for` so it tracks a benchmark that becomes usable again."""
    if job.priority == "upgrade" and not upgrade_wanted(job.requested_quality, plan, best_usable):
        return None
    if job.requested_quality == "both" and job.model in (settings.fast_model, settings.best_model):
        if job.model == settings.best_model and not best_usable:
            return settings.fast_model
        return job.model
    return model_for(job.requested_quality, job.priority, plan, settings, best_usable)


def best_usable(timings: dict[str, ModelTiming], settings: Settings) -> bool:
    """Whether the configured better model can actually be used right now, not merely configured."""
    return best_status(timings.get(settings.best_model)) != "unavailable"


def copy_label(model: str, settings: Settings) -> str:
    """The quality a finished copy reports."""
    return "best" if model == settings.best_model else "fast"
