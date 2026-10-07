from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 44100
STDERR_TAIL_CHARS = 800


def stderr_tail(exc: subprocess.CalledProcessError, limit: int = STDERR_TAIL_CHARS) -> str:
    """The last `limit` chars of a failed child's stderr, for error messages a human can act on."""
    raw = exc.stderr or b""
    text = raw if isinstance(raw, str) else raw.decode(errors="replace")
    return text.strip()[-limit:]


def decode(path: Path, gain_db: float = 0.0) -> np.ndarray:
    """Any file ffmpeg reads → float32 array shaped (2, n) at 44.1 kHz."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-ac", "2", "-ar", str(SAMPLE_RATE),
           "-af", f"volume={gain_db}dB", "-f", "f32le", "-"]
    try:
        raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Could not decode {path.name}: {stderr_tail(exc)}") from exc
    return np.frombuffer(raw, dtype="<f4").reshape(-1, 2).T.copy()


def encode_aac(audio: np.ndarray, dest: Path) -> None:
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "2",
           "-i", "-", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(dest)]
    payload = np.ascontiguousarray(audio.T, dtype="<f4").tobytes()
    try:
        subprocess.run(cmd, check=True, input=payload, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Could not encode {dest.name}: {stderr_tail(exc)}") from exc


def write_float_wav(audio: np.ndarray, dest: Path) -> None:
    sf.write(dest, np.ascontiguousarray(audio.T), SAMPLE_RATE, subtype="FLOAT")


def read_wav(path: Path) -> np.ndarray:
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    if rate != SAMPLE_RATE:
        raise ValueError(f"{path.name}: expected {SAMPLE_RATE} Hz, got {rate}")
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    return data[:, :2].T.copy()
