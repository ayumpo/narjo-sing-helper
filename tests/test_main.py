import pytest
from conftest import FakeRunner

from narjo_sing.__main__ import apply_benchmark, build
from narjo_sing.config import Settings


def test_build_and_apply_benchmark(tmp_path):
    settings = Settings.from_env({"SING_MUSIC_DIR": str(tmp_path / "music"), "SING_MODELS_DIR": str(tmp_path / "models"),
                                  "SING_STEMS_DIR": str(tmp_path / "stems")})
    (tmp_path / "music").mkdir()
    ctx, worker = build(settings, FakeRunner())
    assert (tmp_path / "stems" / "access-key.txt").read_text().strip() == ctx.key
    apply_benchmark(ctx, {"device": "cpu: x", "fast": {"model": "htdemucs", "rt": 0.45, "load_seconds": 2.0},
                          "best": {"model": "bs_roformer", "rt": 17.0, "load_seconds": 11.0}})
    assert ctx.status.plan.mode == "two-tier" and ctx.status.benchmarking is False
    assert worker.status is ctx.status


def test_unknown_model_is_rejected_at_startup(tmp_path):
    settings = Settings.from_env({"SING_STEMS_DIR": str(tmp_path), "SING_FAST_MODEL": "spleeter"})
    with pytest.raises(ValueError, match="choose one of"):
        build(settings, FakeRunner())
