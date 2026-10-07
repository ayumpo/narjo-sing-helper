import numpy as np
import pytest
from conftest import make_flac

from narjo_sing.audio import SAMPLE_RATE, decode, encode_aac, read_wav, write_float_wav
from narjo_sing.stems import read_meta, song_key, stem_path, write_stem_pair


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


def sine(seconds: float, amplitude: float) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    wave = (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    return np.stack([wave, wave])


def test_decode_gives_stereo_44k_float(music):
    audio = decode(make_flac(music / "a.flac", seconds=2))
    assert audio.shape[0] == 2 and audio.dtype == np.float32
    assert abs(audio.shape[1] - 2 * SAMPLE_RATE) < 100


def test_decode_failure_message_includes_stderr_tail(tmp_path):
    with pytest.raises(RuntimeError, match="No such file or directory"):
        decode(tmp_path / "missing.flac")


def test_encode_failure_message_includes_stderr_tail(tmp_path):
    with pytest.raises(RuntimeError):
        encode_aac(sine(0.1, 0.2), tmp_path / "no-such-dir" / "out.m4a")


def test_float_wav_round_trip(tmp_path):
    audio = sine(1, 0.4)
    write_float_wav(audio, tmp_path / "x.wav")
    assert np.allclose(read_wav(tmp_path / "x.wav"), audio, atol=1e-6)


def test_song_key_tracks_file_identity():
    assert song_key("a.flac", 10, 1) != song_key("a.flac", 10, 2)
    assert song_key("a.flac", 10, 1) == song_key("a.flac", 10, 1)


def test_stem_pair_adds_back_to_the_source(stems):
    source = sine(2, 0.6)
    vocals = source * 0.3
    meta = write_stem_pair(stems, "k1", source, vocals, rel_path="s.flac", model="htdemucs", quality="fast")
    assert read_meta(stems, "k1") == meta
    assert meta.quality == "fast" and abs(meta.duration - 2.0) < 0.01
    v = decode(stem_path(stems, meta, "vocals"))[:, 4096:SAMPLE_RATE]
    i = decode(stem_path(stems, meta, "instrumental"))[:, 4096:SAMPLE_RATE]
    assert abs(rms(v) - rms(vocals[:, 4096:SAMPLE_RATE])) / rms(vocals) < 0.05
    assert abs(rms(i) - rms(source[:, 4096:SAMPLE_RATE] * 0.7)) / rms(source) < 0.05


def test_better_stems_replace_the_previous_version(stems):
    source = sine(1, 0.5)
    first = write_stem_pair(stems, "k", source, source * 0.5, rel_path="x", model="htdemucs", quality="fast")
    second = write_stem_pair(stems, "k", source, source * 0.4, rel_path="x", model="bs_roformer", quality="best")
    assert read_meta(stems, "k").etag == second.etag != first.etag
    assert not (stems / "k" / first.etag).exists()
    assert stem_path(stems, second, "vocals").exists()


def test_headroom_keeps_stems_below_full_scale(stems):
    source = sine(1, 0.9)
    meta = write_stem_pair(stems, "h", source, -source * 0.5, rel_path="x", model="m", quality="fast")
    assert np.abs(decode(stem_path(stems, meta, "instrumental"))).max() < 1.05


def test_encode_failure_leaves_no_orphan_folder(stems, monkeypatch):
    source = sine(1, 0.5)

    def fail_encode(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr("narjo_sing.stems.encode_aac", fail_encode)
    with pytest.raises(OSError):
        write_stem_pair(stems, "k", source, source * 0.5, rel_path="x", model="m", quality="fast")
    song_dir = stems / "k"
    assert not song_dir.exists() or list(song_dir.iterdir()) == []


def test_unknown_stem_name_is_rejected(stems):
    meta = write_stem_pair(stems, "u", sine(1, 0.2), sine(1, 0.1), rel_path="x", model="m", quality="fast")
    with pytest.raises(ValueError):
        stem_path(stems, meta, "drums")
