"""Stem pairs live in stems/<key>/<etag>/; stems/<key>/meta.json names the current pair."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .audio import SAMPLE_RATE, encode_aac

STEM_NAMES = ("vocals", "instrumental")
FULL_SCALE = 0.999


@dataclass(frozen=True)
class StemMeta:
    key: str
    etag: str
    quality: str
    model: str
    rel_path: str
    duration: float
    created: float


def song_key(rel_path: str, size: int, mtime_ns: int) -> str:
    return hashlib.sha1(f"{rel_path}\0{size}\0{mtime_ns}".encode()).hexdigest()


def read_meta(stems_dir: Path, key: str) -> StemMeta | None:
    try:
        return StemMeta(**json.loads((stems_dir / key / "meta.json").read_text()))
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError, TypeError):
        return None


def stem_path(stems_dir: Path, meta: StemMeta, name: str) -> Path:
    if name not in STEM_NAMES:
        raise ValueError(f"Unknown stem {name!r}")
    return stems_dir / meta.key / meta.etag / f"{name}.m4a"


def write_stem_pair(stems_dir: Path, key: str, source: np.ndarray, vocals: np.ndarray, *,
                    rel_path: str, model: str, quality: str) -> StemMeta:
    frames = min(source.shape[1], vocals.shape[1])
    vocals = vocals[:, :frames].astype(np.float32)
    instrumental = source[:, :frames].astype(np.float32) - vocals
    peak = float(max(np.abs(vocals).max(initial=0.0), np.abs(instrumental).max(initial=0.0)))
    # One shared scale keeps vocals + instrumental proportional to the source.
    scale = FULL_SCALE / peak if peak > FULL_SCALE else 1.0
    etag = uuid.uuid4().hex[:16]
    song_dir = stems_dir / key
    version_dir = song_dir / etag
    version_dir.mkdir(parents=True)
    encode_aac(vocals * scale, version_dir / "vocals.m4a")
    encode_aac(instrumental * scale, version_dir / "instrumental.m4a")
    meta = StemMeta(key, etag, quality, model, rel_path, frames / SAMPLE_RATE, time.time())
    previous = read_meta(stems_dir, key)
    staged = song_dir / f"meta.json.{etag}.tmp"
    staged.write_text(json.dumps(asdict(meta)))
    os.replace(staged, song_dir / "meta.json")
    if previous is not None and previous.etag != etag:
        shutil.rmtree(song_dir / previous.etag, ignore_errors=True)
    return meta
