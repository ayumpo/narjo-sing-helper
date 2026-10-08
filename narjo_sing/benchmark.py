from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .audio import SAMPLE_RATE
from .config import Settings
from .status import ModelTiming
from .tiers import UPGRADE_MAX_RT

log = logging.getLogger(__name__)

BENCH_SECONDS = 9.0
DOWNLOAD_TIMEOUT = 3600.0
LOAD_TIMEOUT = 600.0
RETRY_DELAYS = (30.0, 120.0)  # 3 attempts total: try, wait 30s, try, wait 120s, try


def synthetic_clip(seconds: float = BENCH_SECONDS) -> np.ndarray:
    rng = np.random.default_rng(7)
    frames = int(seconds * SAMPLE_RATE)
    t = np.arange(frames) / SAMPLE_RATE
    tone = 0.2 * np.sin(2 * np.pi * 220 * t)
    return (tone + 0.05 * rng.standard_normal((2, frames))).astype(np.float32)


def time_model(runner, model_key: str, background: bool, max_rt: float | None = None,
               sleep=time.sleep, clock=time.monotonic) -> ModelTiming:
    runner.prepare(model_key, timeout=DOWNLOAD_TIMEOUT)  # download + first load, untimed
    load = runner.prepare(model_key, timeout=LOAD_TIMEOUT)
    run = runner.start(model_key, synthetic_clip(), background)
    started = clock()
    cap = None if max_rt is None else load + max_rt * BENCH_SECONDS
    while not run.poll():
        if cap is not None and clock() - started > cap:
            run.cancel()
            return ModelTiming(model_key, None, load)
        sleep(0.5)
    run.result()
    return ModelTiming(model_key, max(0.01, (clock() - started - load) / BENCH_SECONDS), load)


def _with_retries(fn, sleep=time.sleep):
    for attempt, delay in enumerate((0.0,) + RETRY_DELAYS):
        if delay:
            sleep(delay)
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            log.warning("Benchmark attempt %d/%d failed: %s", attempt + 1, len(RETRY_DELAYS) + 1, exc)
    raise last_exc


def load_or_run(path: Path, runner, settings: Settings, version: str, force: bool = False,
                sleep=time.sleep) -> dict:
    if path.exists() and not force:
        cached = json.loads(path.read_text())
        if (cached.get("version"), cached.get("fast", {}).get("model"), cached.get("best", {}).get("model")) == (
                version, settings.fast_model, settings.best_model):
            return cached
    device = _with_retries(runner.device, sleep=sleep)
    fast = _with_retries(lambda: time_model(runner, settings.fast_model, background=False), sleep=sleep)
    best = fast if settings.best_model == settings.fast_model else None
    try:
        if best is None:
            best = _with_retries(
                lambda: time_model(runner, settings.best_model, background=True, max_rt=UPGRADE_MAX_RT), sleep=sleep)
    except Exception as exc:
        log.warning("Best-tier benchmark failed after retries; continuing fast-only: %s", exc)
        best = ModelTiming(settings.best_model, None, 0.0)
    result = {
        "version": version,
        "device": device,
        "fast": asdict(fast),
        "best": asdict(best),
    }
    path.write_text(json.dumps(result, indent=2))
    return result
