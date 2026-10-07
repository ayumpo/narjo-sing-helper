from pathlib import Path

import pytest

from narjo_sing.auth import load_or_create_key
from narjo_sing.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert (s.music_dir, s.models_dir, s.stems_dir) == (Path("/music"), Path("/models"), Path("/stems"))
    assert (s.port, s.stem_cache_gb, s.rescan_minutes) == (8765, 50.0, 10)
    assert (s.fast_model, s.best_model, s.best_upgrade, s.best_hours) == ("htdemucs", "bs_roformer", "auto", None)


def test_env_overrides():
    s = Settings.from_env({
        "SING_MUSIC_DIR": "/volume1/data/Media/music", "SING_BEST_UPGRADE": "ON",
        "SING_BEST_HOURS": "01:00-07:00", "SING_STEM_CACHE_GB": "5", "SING_FAST_MODEL": "kim_vocal_2",
    })
    assert s.music_dir == Path("/volume1/data/Media/music")
    assert (s.best_upgrade, s.best_hours, s.stem_cache_gb, s.fast_model) == ("on", "01:00-07:00", 5.0, "kim_vocal_2")


def test_rejects_unknown_upgrade_value():
    with pytest.raises(ValueError, match="SING_BEST_UPGRADE"):
        Settings.from_env({"SING_BEST_UPGRADE": "maybe"})


def test_key_is_created_once_and_private(tmp_path):
    first = load_or_create_key(tmp_path)
    assert load_or_create_key(tmp_path) == first
    assert len(first) >= 32
    assert (tmp_path / "access-key.txt").stat().st_mode & 0o777 == 0o600
