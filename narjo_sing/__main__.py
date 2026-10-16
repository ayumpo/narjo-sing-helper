from __future__ import annotations

import logging
import os
import threading
import time

import uvicorn

from . import __version__
from .api import AppContext, create_app
from .auth import load_or_create_key
from .benchmark import load_or_run
from .config import Settings
from .jobs import JobStore
from .library.index import LibraryIndex
from .music_folder import check_music_folder
from .runner import SubprocessRunner, clean_work_dir
from .separation import model_spec
from .status import HelperStatus, ModelTiming
from .tiers import decide
from .worker import Worker

log = logging.getLogger("narjo_sing")


def build(settings: Settings, runner) -> tuple[AppContext, Worker]:
    model_spec(settings.fast_model)
    model_spec(settings.best_model)
    for directory in (settings.models_dir, settings.stems_dir):
        directory.mkdir(parents=True, exist_ok=True)
    clean_work_dir(settings.stems_dir / ".work")
    key = load_or_create_key(settings.stems_dir)
    index = LibraryIndex(settings.stems_dir / "index.sqlite", settings.music_dir)
    store = JobStore(settings.stems_dir / "jobs.sqlite")
    status = HelperStatus()
    ctx = AppContext(settings, key, index, store, status, __version__)
    return ctx, Worker(store, index, runner, settings, status)


def apply_benchmark(ctx: AppContext, bench: dict) -> None:
    s = ctx.settings
    ctx.status.device = bench["device"]
    ctx.status.timings = {t["model"]: ModelTiming(**t) for t in (bench["fast"], bench["best"])}
    ctx.status.plan = decide(s.fast_model, s.best_model, bench["fast"]["rt"], bench["best"]["rt"], s.best_upgrade)
    ctx.status.benchmarking = False


def scan_once(index: LibraryIndex, status: HelperStatus) -> None:
    """Scans the library unless the music folder can't be used; then the index keeps its songs."""
    if check_music_folder(status, index.music_dir, index.count):
        return
    log.info("Library scan: %s", index.scan())


MUSIC_CHECK_SECONDS = 20


def music_watch_loop(index: LibraryIndex, status: HelperStatus, stop: threading.Event,
                     seconds: float = MUSIC_CHECK_SECONDS) -> None:
    """Keeps `status.music_problem` current for the API, which reads it instead of touching the folder."""
    while not stop.is_set():
        try:
            check_music_folder(status, index.music_dir, index.count)
        except Exception:
            log.exception("Music folder check failed")
        stop.wait(seconds)


# A scan stats every file; on a slow share it can take minutes, so the pause after it grows with it (the share
# is busy scanning at most about a sixth of the time), up to an hour so new and moved songs still show up.
SCAN_PAUSE_FACTOR = 5
SCAN_PAUSE_CEILING_SECONDS = 3600


def scan_wait_seconds(minutes: int, last_scan_seconds: float) -> float:
    return max(minutes * 60, min(SCAN_PAUSE_FACTOR * last_scan_seconds, SCAN_PAUSE_CEILING_SECONDS))


def scan_loop(index: LibraryIndex, minutes: int, stop: threading.Event, status: HelperStatus,
              clock=time.monotonic) -> None:
    while not stop.is_set():
        started = clock()
        try:
            scan_once(index, status)
        except Exception:
            log.exception("Library scan failed")
        stop.wait(scan_wait_seconds(minutes, clock() - started))


def worker_loop(ctx: AppContext, worker: Worker, runner, force_bench: bool, stop: threading.Event,
                sleep=time.sleep) -> None:
    try:
        bench = load_or_run(ctx.settings.stems_dir / "benchmark.json", runner, ctx.settings, ctx.version,
                            force=force_bench, sleep=sleep)
        apply_benchmark(ctx, bench)
        log.info("Device %s; plan %s", ctx.status.device, ctx.status.plan)
    except Exception as exc:
        log.exception("Benchmark failed; the helper cannot separate until this is fixed")
        ctx.status.error = f"Benchmark failed: {exc}"[:500]
        ctx.status.benchmarking = False
        return
    worker.run_forever(stop)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    runner = SubprocessRunner(settings.models_dir, settings.stems_dir / ".work")
    ctx, worker = build(settings, runner)
    log.info("Access key (also saved in %s): %s", settings.stems_dir / "access-key.txt", ctx.key)
    stop = threading.Event()
    threading.Thread(target=music_watch_loop, args=(ctx.index, ctx.status, stop), daemon=True).start()
    threading.Thread(target=scan_loop, args=(ctx.index, settings.rescan_minutes, stop, ctx.status),
                     daemon=True).start()
    threading.Thread(target=worker_loop, args=(ctx, worker, runner, os.environ.get("SING_REBENCHMARK") == "1", stop),
                     daemon=True).start()
    uvicorn.run(create_app(ctx), host="0.0.0.0", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
