from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from .audio import SAMPLE_RATE, decode
from .cache import evict
from .config import Settings
from .jobs import JobStore
from .library.index import LibraryIndex
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

    def step(self) -> bool:
        plan = self.status.plan
        if plan is None:
            return False
        job = self.store.next_runnable(allow_background=within_hours(self.settings.best_hours, self._now()))
        if job is None:
            return False
        file = self.index.get(job.rel_path)
        if file is None or song_key(file.rel_path, file.size, file.mtime_ns) != job.key:
            self.store.fail(job.id, "The file changed or was removed after the request; submit the song again.")
            return True
        self.store.mark_running(job.id)
        try:
            source = decode(self.settings.music_dir / job.rel_path)
        except Exception as exc:
            self.store.fail(job.id, f"Could not decode the file: {exc}"[:500])
            return True
        expected = self.status.expected_seconds(job.model, source.shape[1] / SAMPLE_RATE)
        run = self.runner.start(job.model, source, background=job.background)
        started = self._clock()
        while not run.poll():
            if job.background and self.store.has_urgent_queued():
                run.cancel()
                self.store.requeue(job.id)
                log.info("Paused %s for an urgent request", job.rel_path)
                return True
            elapsed = self._clock() - started
            self.store.update_progress(job.id, min(0.95, elapsed / expected), max(0.0, expected - elapsed))
            self._sleep(1.0)
        try:
            vocals = run.result()
        except Exception as exc:
            self.store.fail(job.id, str(exc)[:500])
            return True
        quality = "best" if job.model == plan.best_model else "fast"
        try:
            write_stem_pair(self.settings.stems_dir, job.key, source, vocals, rel_path=job.rel_path, model=job.model,
                            quality=quality)
        except Exception as exc:
            self.store.fail(job.id, f"Could not save the stems: {exc}"[:500])
            log.exception("Failed to save stems for %s", job.rel_path)
            return True
        self.store.finish(job.id)
        self.store.touch(job.key)
        if plan.upgrade and quality == "fast":
            self.store.submit(job.key, job.rel_path, job.duration, "upgrade", plan.best_model, background=True)
        try:
            evict(self.settings.stems_dir, int(self.settings.stem_cache_gb * 1e9), self.store.usage(),
                  self.store.active_keys())
        except Exception:
            log.exception("Stem cache eviction failed")
        log.info("Separated %s with %s (%s)", job.rel_path, job.model, quality)
        return True

    def run_forever(self, stop: threading.Event) -> None:
        self.store.recover()
        while not stop.is_set():
            try:
                worked = self.step()
            except Exception:
                log.exception("Worker step failed")
                worked = False
            if not worked:
                stop.wait(2.0)
