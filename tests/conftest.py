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
    def __init__(self, source, polls_needed: int, fail: str | None, on_poll):
        self.source = source
        self.polls = 0
        self.polls_needed = polls_needed
        self.fail = fail
        self.on_poll = on_poll
        self.cancelled = False

    def poll(self) -> bool:
        self.polls += 1
        if self.on_poll:
            self.on_poll(self.polls)
        return self.polls >= self.polls_needed

    def result(self):
        if self.fail:
            raise RuntimeError(self.fail)
        return (self.source * 0.25).astype(np.float32)

    def cancel(self) -> None:
        self.cancelled = True


class FakeRunner:
    def __init__(self, polls_needed: int = 1, fail: str | None = None, load_seconds: float = 2.0, on_poll=None):
        self.polls_needed = polls_needed
        self.fail = fail
        self.load_seconds = load_seconds
        self.on_poll = on_poll
        self.started: list[tuple[str, bool, FakeRun]] = []
        self.prepared: list[str] = []

    def device(self) -> str:
        return "cpu: fake"

    def prepare(self, model_key: str, timeout: float) -> float:
        self.prepared.append(model_key)
        return self.load_seconds

    def start(self, model_key: str, source, background: bool) -> FakeRun:
        run = FakeRun(source, self.polls_needed, self.fail, self.on_poll)
        self.started.append((model_key, background, run))
        return run
