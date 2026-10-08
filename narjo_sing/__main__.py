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
    if moved := ctx.store.retarget_queued(ctx.status.plan):
        log.info("Moved %d queued jobs to the configured models", moved)
    ctx.status.benchmarking = False


def scan_loop(index: LibraryIndex, minutes: int, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            log.info("Library scan: %s", index.scan())
        except Exception:
            log.exception("Library scan failed")
        stop.wait(minutes * 60)


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
    threading.Thread(target=scan_loop, args=(ctx.index, settings.rescan_minutes, stop), daemon=True).start()
    threading.Thread(target=worker_loop, args=(ctx, worker, runner, os.environ.get("SING_REBENCHMARK") == "1", stop),
                     daemon=True).start()
    uvicorn.run(create_app(ctx), host="0.0.0.0", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
