import subprocess
from pathlib import Path

import numpy as np
import pytest
from mutagen.flac import FLAC


def make_flac(path: Path, seconds: float = 3.0, freq: float = 440.0, **tags) -> Path:
    """A stereo 44.1 kHz sine FLAC with Vorbis comment tags (keys as given, e.g. ALBUMARTIST)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={seconds}", "-ac", "2", "-ar", "44100", str(path)],
        check=True,
    )
    if tags:
        audio = FLAC(path)
        for key, value in tags.items():
            audio[key] = str(value)
        audio.save()
    return path


@pytest.fixture
def music(tmp_path: Path) -> Path:
    directory = tmp_path / "music"
    directory.mkdir()
    return directory


@pytest.fixture
def stems(tmp_path: Path) -> Path:
    directory = tmp_path / "stems"
    directory.mkdir()
    return directory


class FakeRun:
    def __init__(self, source, polls_needed: int, fail: str | None, on_poll, poll_fail_after: int | None = None):
        self.source = source
        self.polls = 0
        self.work = 0
        self.polls_needed = polls_needed
        self.fail = fail
        self.on_poll = on_poll
        self.poll_fail_after = poll_fail_after
        self.cancelled = False
        self.suspended = False
        self.suspends = 0
        self.resumes = 0

    def poll(self) -> bool:
        self.polls += 1
        if self.poll_fail_after is not None and self.polls >= self.poll_fail_after:
            raise RuntimeError("poll blew up")
        if self.on_poll:
            self.on_poll(self.polls)
        if not self.suspended:
            self.work += 1
        return self.work >= self.polls_needed

    def result(self):
        if self.fail:
            raise RuntimeError(self.fail)
        return (self.source * 0.25).astype(np.float32)

    def suspend(self) -> None:
        self.suspended = True
        self.suspends += 1

    def resume(self) -> None:
        self.suspended = False
        self.resumes += 1

    def cancel(self) -> None:
        self.cancelled = True


class FakeRunner:
    def __init__(self, polls_needed: int = 1, fail: str | None = None, load_seconds: float = 2.0, on_poll=None,
                 fail_start: str | None = None, poll_fail_after: int | None = None,
                 fail_prepare_models: set[str] | None = None, fail_device: bool = False):
        self.polls_needed = polls_needed
        self.fail = fail
        self.load_seconds = load_seconds
        self.on_poll = on_poll
        self.fail_start = fail_start
        self.poll_fail_after = poll_fail_after
        self.fail_prepare_models = fail_prepare_models or set()
        self.fail_device = fail_device
        self.started: list[tuple[str, bool, FakeRun]] = []
        self.prepared: list[str] = []

    def device(self) -> str:
        if self.fail_device:
            raise RuntimeError("device detection blew up")
        return "cpu: fake"

    def prepare(self, model_key: str, timeout: float) -> float:
        self.prepared.append(model_key)
        if model_key in self.fail_prepare_models:
            raise RuntimeError(f"{model_key} prepare blew up")
        return self.load_seconds

    def start(self, model_key: str, source, background: bool) -> FakeRun:
        if self.fail_start:
            raise RuntimeError(self.fail_start)
        run = FakeRun(source, self.polls_needed, self.fail, self.on_poll, self.poll_fail_after)
        self.started.append((model_key, background, run))
        return run
