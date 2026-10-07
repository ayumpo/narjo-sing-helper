from types import SimpleNamespace

import numpy as np

from narjo_sing.audio import SAMPLE_RATE
from narjo_sing.cache import RESERVE_BYTES, effective_budget, evict
from narjo_sing.stems import write_stem_pair


def pair(stems, key):
    t = np.arange(SAMPLE_RATE) / SAMPLE_RATE
    wave = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    source = np.stack([wave, wave])
    write_stem_pair(stems, key, source, source * 0.5, rel_path=key, model="m", quality="fast")


def size_of(stems, key):
    return sum(f.stat().st_size for f in (stems / key).rglob("*") if f.is_file())


def test_least_recently_used_goes_first_and_active_keys_stay(stems):
    for key in ("old", "mid", "new"):
        pair(stems, key)
    (stems / ".work").mkdir()
    budget = size_of(stems, "new") + size_of(stems, "mid") + 10
    removed = evict(stems, budget, {"old": 1.0, "mid": 2.0, "new": 3.0}, protect=set())
    assert removed == ["old"] and (stems / "mid").exists() and (stems / ".work").exists()
    removed = evict(stems, 0, {"mid": 2.0, "new": 3.0}, protect={"mid"})
    assert removed == ["new"] and (stems / "mid").exists()


def test_effective_budget_is_capped_by_free_disk_space(stems, monkeypatch):
    pair(stems, "a")
    current = size_of(stems, "a")
    free = 10_000_000_000
    monkeypatch.setattr("narjo_sing.cache.shutil.disk_usage",
                        lambda path: SimpleNamespace(total=0, used=0, free=free))
    budget = effective_budget(stems, configured_bytes=999_000_000_000)
    assert budget == current + free - RESERVE_BYTES


def test_effective_budget_never_goes_negative(stems, monkeypatch):
    pair(stems, "a")
    monkeypatch.setattr("narjo_sing.cache.shutil.disk_usage",
                        lambda path: SimpleNamespace(total=0, used=0, free=0))
    assert effective_budget(stems, configured_bytes=999_000_000_000) == 0


def test_effective_budget_respects_the_configured_cap(stems, monkeypatch):
    pair(stems, "a")
    monkeypatch.setattr("narjo_sing.cache.shutil.disk_usage",
                        lambda path: SimpleNamespace(total=0, used=0, free=1_000_000_000_000))
    assert effective_budget(stems, configured_bytes=5_000_000_000) == 5_000_000_000
