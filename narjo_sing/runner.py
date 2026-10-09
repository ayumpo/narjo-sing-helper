"""Parent side of separation: one child process per job, so jobs can be niced and stopped."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import numpy as np

from .audio import read_wav, stderr_tail, write_float_wav

# The child gets the source 6 dB down so no engine stage clips or renormalizes it.
HEADROOM = 0.5


def clean_work_dir(work_dir: Path) -> None:
    """Removes per-job run folders left behind by a crash; call once at startup, before the worker runs."""
    if not work_dir.exists():
        return
    for entry in work_dir.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


class SubprocessRun:
    def __init__(self, process: subprocess.Popen, run_dir: Path, log):
        self.process = process
        self.run_dir = run_dir
        self._log = log
        self.suspended = False

    def poll(self) -> bool:
        return self.process.poll() is not None

    def result(self) -> np.ndarray:
        self.process.wait()
        self._log.close()
        try:
            if self.process.returncode != 0:
                lines = (self.run_dir / "child.log").read_text(errors="replace").strip().splitlines()
                raise RuntimeError("Separation failed: " + " | ".join(lines[-5:]))
            return read_wav(self.run_dir / "vocals.wav") / HEADROOM
        finally:
            shutil.rmtree(self.run_dir, ignore_errors=True)

    def suspend(self) -> None:
        """Freezes the separation where it is: its CPU is free until `resume`, its memory stays in use."""
        self._signal(signal.SIGSTOP)
        self.suspended = True

    def resume(self) -> None:
        self._signal(signal.SIGCONT)
        self.suspended = False

    def cancel(self) -> None:
        self._signal(signal.SIGTERM)
        if self.suspended:
            # A stopped process acts on SIGTERM only once it runs again.
            self._signal(signal.SIGCONT)
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._signal(signal.SIGKILL)
            self.process.wait()
        self._log.close()
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def _signal(self, sig: int) -> None:
        # The child leads its own process group (`start_new_session`), so anything it started gets the signal too.
        # Once reaped, its pid may belong to another process, so nothing is sent.
        if self.process.returncode is not None:
            return
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass


class SubprocessRunner:
    def __init__(self, models_dir: Path, work_dir: Path, child_cmd: list[str] | None = None):
        self.models_dir = models_dir
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.child_cmd = child_cmd or [sys.executable, "-m", "narjo_sing.separate_cli"]

    def device(self, timeout: float = 300) -> str:
        try:
            out = subprocess.run(self.child_cmd + ["device"], capture_output=True, text=True, timeout=timeout,
                                 check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Could not detect the device: {stderr_tail(exc)}") from exc
        return out.stdout.strip().splitlines()[-1]

    def prepare(self, model_key: str, timeout: float) -> float:
        started = time.monotonic()
        try:
            subprocess.run(self.child_cmd + ["prepare", "--model", model_key, "--models-dir", str(self.models_dir)],
                           check=True, timeout=timeout, capture_output=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Could not prepare {model_key}: {stderr_tail(exc)}") from exc
        return time.monotonic() - started

    def build_command(self, model_key: str, input_wav: Path, output_wav: Path, background: bool) -> list[str]:
        nice = ["nice", "-n", "19"] if background else []
        return nice + self.child_cmd + ["separate", "--model", model_key, "--input", str(input_wav),
                                        "--output", str(output_wav), "--models-dir", str(self.models_dir)]

    def start(self, model_key: str, source: np.ndarray, background: bool) -> SubprocessRun:
        run_dir = self.work_dir / uuid.uuid4().hex
        run_dir.mkdir()
        write_float_wav(source * HEADROOM, run_dir / "input.wav")
        log = open(run_dir / "child.log", "wb")
        command = self.build_command(model_key, run_dir / "input.wav", run_dir / "vocals.wav", background)
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        return SubprocessRun(process, run_dir, log)
