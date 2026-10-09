from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from .audio import SAMPLE_RATE, decode
from .cache import effective_budget, evict
from .config import Settings
from .jobs import Job, JobStore
from .library.index import LibraryIndex
from .quality import best_status, copy_label, queued_model, upgrade_wanted
from .status import HelperStatus
from .stems import song_key, write_stem_pair
from .tiers import within_hours

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, store: JobStore, index: LibraryIndex, runner, settings: Settings, status: HelperStatus,
                 sleep=time.sleep, now=lambda: datetime.now().time(), clock=time.monotonic):
        self.store = store
        self.index = index
        self.runner = runner
        self.settings = settings
        self.status = status
        self._sleep = sleep
        self._now = now
        self._clock = clock

    def _best_usable(self) -> bool:
        return best_status(self.status.timings.get(self.settings.best_model)) != "unavailable"

    def step(self) -> bool:
        plan = self.status.plan
        if plan is None or self.store.paused():
            return False
        job = self.store.next_runnable(allow_background=within_hours(self.settings.best_hours, self._now()))
        if job is None:
            return False
        file = self.index.get(job.rel_path)
        if file is None or song_key(file.rel_path, file.size, file.mtime_ns) != job.key:
            self.store.fail(job.id, "The file changed or was removed after the request; submit the song again.")
            return True
        max_seconds = self.settings.max_minutes * 60
        if file.duration > max_seconds:
            self.store.fail(job.id, f"Longer than SING_MAX_MINUTES ({self.settings.max_minutes:g} min)")
            return True
        if not self.store.mark_running(job.id):
            return True  # cancelled between being picked and starting
        run = None
        try:
            try:
                source = decode(self.settings.music_dir / job.rel_path)
            except Exception as exc:
                self.store.fail(job.id, f"Could not decode the file: {exc}"[:500])
                log.exception("Could not decode %s (job %s)", job.rel_path, job.id)
                return True
            expected = self.status.expected_seconds(job.model, source.shape[1] / SAMPLE_RATE)
            run = self.runner.start(job.model, source, background=job.background)
            if not self._wait(job, run, expected):
                return True
            try:
                vocals = run.result()
            except Exception as exc:
                self.store.fail(job.id, str(exc)[:500])
                log.exception("Separation failed for %s (job %s)", job.rel_path, job.id)
                return True
            quality = copy_label(job.model, self.settings)
            try:
                write_stem_pair(self.settings.stems_dir, job.key, source, vocals, rel_path=job.rel_path,
                                model=job.model, quality=quality)
            except Exception as exc:
                self.store.fail(job.id, f"Could not save the stems: {exc}"[:500])
                log.exception("Failed to save stems for %s (job %s)", job.rel_path, job.id)
                self._evict()
                return True
        except Exception as exc:
            if run is not None:
                try:
                    run.cancel()
                except Exception:
                    log.exception("Failed to cancel the run for %s (job %s)", job.rel_path, job.id)
            self.store.fail(job.id, f"Unexpected worker error: {exc}"[:500])
            log.exception("Worker step failed for %s (job %s)", job.rel_path, job.id)
            return True
        self.store.finish(job.id)
        # The stems are published and the job is done; a failure below must not turn it into "failed".
        try:
            self.store.touch(job.key)
            # Automatic queues the better copy after the fast one; Both queued it with the request.
            if quality == "fast" and job.requested_quality == "auto" and upgrade_wanted("auto", plan,
                                                                                         self._best_usable()):
                self.store.submit(job.key, job.rel_path, job.duration, "upgrade", plan.best_model, background=True)
        except Exception:
            log.exception("Bookkeeping after %s failed (job %s)", job.rel_path, job.id)
        self._evict()
        log.info("Separated %s with %s (%s)", job.rel_path, job.model, quality)
        return True

    def _wait(self, job: Job, run, expected: float) -> bool:
        """Polls `run` until it finishes. False when it was cancelled or set aside for an urgent request. While
        the helper is paused the run is frozen, and the paused time counts neither as progress nor toward the ETA."""
        started = self._clock()
        paused_for = 0.0
        paused_at = None
        while not run.poll():
            if self.store.is_cancelled(job.id):
                run.cancel()
                log.info("Cancelled %s", job.rel_path)
                return False
            if self.store.paused():
                if paused_at is None:
                    run.suspend()
                    paused_at = self._clock()
                    log.info("Paused %s", job.rel_path)
                self._sleep(1.0)
                continue
            if paused_at is not None:
                run.resume()
                paused_for += self._clock() - paused_at
                paused_at = None
                log.info("Resumed %s", job.rel_path)
            if job.background and self.store.has_urgent_queued():
                run.cancel()
                self.store.requeue(job.id)
                log.info("Set %s aside for an urgent request", job.rel_path)
                return False
            elapsed = self._clock() - started - paused_for
            self.store.update_progress(job.id, min(0.95, elapsed / expected), max(0.0, expected - elapsed))
            self._sleep(1.0)
        return True

    def _evict(self) -> None:
        try:
            budget = effective_budget(self.settings.stems_dir, int(self.settings.stem_cache_gb * 1e9))
            evict(self.settings.stems_dir, budget, self.store.usage(), self.store.active_keys())
        except Exception:
            log.exception("Stem cache eviction failed")

    def run_forever(self, stop: threading.Event) -> None:
        self.store.recover()
        # After recover, so a job a restart interrupted also follows the configured models and the benchmark.
        plan = self.status.plan
        if plan is not None:
            usable = self._best_usable()
            if moved := self.store.retarget_queued(lambda job: queued_model(job, plan, self.settings, usable)):
                log.info("Moved %d queued jobs to the configured models", moved)
        while not stop.is_set():
            try:
                worked = self.step()
            except Exception:
                log.exception("Worker step failed")
                worked = False
            if not worked:
                stop.wait(2.0)
