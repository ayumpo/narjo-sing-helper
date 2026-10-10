"""Model catalog and the child-process side of separation. audio_separator is imported lazily."""

from __future__ import annotations

import os
import platform
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ModelSpec:
    key: str
    filename: str
    separator_kwargs: dict = field(default_factory=dict)


CATALOG: dict[str, ModelSpec] = {
    "htdemucs": ModelSpec("htdemucs", "htdemucs.yaml", {"demucs_params": {
        "segment_size": "Default", "shifts": 1, "overlap": 0.25, "segments_enabled": True}}),
    "melband_kim": ModelSpec("melband_kim", "vocals_mel_band_roformer.ckpt"),
}


def model_spec(key: str) -> ModelSpec:
    try:
        return CATALOG[key]
    except KeyError:
        raise ValueError(f"Unknown model {key!r}; choose one of {', '.join(CATALOG)}") from None


def _separator(model_key: str, models_dir: Path, output_dir: Path):
    from audio_separator.separator import Separator

    spec = model_spec(model_key)
    separator = Separator(
        model_file_dir=str(models_dir), output_dir=str(output_dir), output_format="WAV",
        normalization_threshold=1.0, output_single_stem="Vocals", sample_rate=44100,
        **spec.separator_kwargs,
    )
    separator.load_model(model_filename=spec.filename)
    return separator


def prepare_model(model_key: str, models_dir: Path) -> None:
    """Downloads (first time) and loads the model, then exits."""
    with tempfile.TemporaryDirectory() as work:
        _separator(model_key, models_dir, Path(work))


def separate_vocals_file(model_key: str, input_wav: Path, output_wav: Path, models_dir: Path) -> None:
    with tempfile.TemporaryDirectory(dir=output_wav.parent) as work:
        separator = _separator(model_key, models_dir, Path(work))
        # audio-separator names the stem "Vocals" or "vocals" depending on the model; both map to one file name.
        outputs = separator.separate(str(input_wav), custom_output_names={"Vocals": "vocals", "vocals": "vocals"})
        produced = [Path(work) / Path(p).name for p in outputs]
        vocals = next((p for p in produced if p.exists() and (p.stem.lower() == "vocals"
                                                             or "(vocals)" in p.name.lower())), None)
        if vocals is None:
            raise RuntimeError(f"{model_spec(model_key).filename} produced no vocals stem: {[p.name for p in produced]}")
        shutil.move(str(vocals), str(output_wav))


def detect_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return f"cuda: {torch.cuda.get_device_name(0)}"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps: Apple silicon"
    except ImportError:
        pass
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
    except OSError:
        cpuinfo = ""
    return f"cpu: {cpu_name(cpuinfo, platform.processor() or platform.machine(), os.cpu_count())}"


def cpu_name(cpuinfo: str, machine: str, cores: int | None) -> str:
    """The CPU's model name. ARM Linux (Docker on a Mac, a Raspberry Pi) reports none, so the maker or the
    architecture stands in, with the core count; implementer 0x61 is Apple."""
    for line in cpuinfo.splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    if re.search(r"^CPU implementer\s*:\s*0x61\b", cpuinfo, re.M):
        vendor = "Apple silicon"
    else:
        vendor = machine or "unknown"
    return f"{vendor}, {cores} cores" if cores and vendor != "unknown" else vendor
