import sys

import numpy as np
import pytest

from narjo_sing.audio import SAMPLE_RATE
from narjo_sing.runner import SubprocessRunner, clean_work_dir
from narjo_sing.separation import CATALOG, model_spec

STUB_CHILD = """
import os, shutil, sys, time
args = sys.argv[1:]
if args[0] == "device":
    print("cpu: stub")
    sys.exit(0)
if args[0] == "prepare":
    sys.exit(0)
if os.environ.get("STUB_SLEEP"):
    time.sleep(60)
if os.environ.get("STUB_FAIL"):
    print("model exploded")
    sys.exit(3)
shutil.copyfile(args[args.index("--input") + 1], args[args.index("--output") + 1])
"""


@pytest.fixture
def runner(tmp_path):
    stub = tmp_path / "stub_child.py"
    stub.write_text(STUB_CHILD)
    return SubprocessRunner(tmp_path / "models", tmp_path / "work", child_cmd=[sys.executable, str(stub)])


def tone(seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    wave = (0.5 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    return np.stack([wave, wave])


def test_catalog():
    assert set(CATALOG) == {"htdemucs", "kim_vocal_2", "bs_roformer"}
    assert CATALOG["htdemucs"].separator_kwargs["demucs_params"]["shifts"] == 1
    assert CATALOG["bs_roformer"].filename == "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
    with pytest.raises(ValueError, match="choose one of"):
        model_spec("nope")


def test_round_trip_restores_unity_gain(runner):
    source = tone()
    run = runner.start("htdemucs", source, background=False)
    while not run.poll():
        pass
    assert np.allclose(run.result(), source, atol=1e-5)
    assert list((runner.work_dir).iterdir()) == []


def test_background_jobs_run_niced(runner, tmp_path):
    cmd = runner.build_command("bs_roformer", tmp_path / "in.wav", tmp_path / "out.wav", background=True)
    assert cmd[:3] == ["nice", "-n", "19"]
    assert runner.build_command("htdemucs", tmp_path / "in.wav", tmp_path / "out.wav", background=False)[0] != "nice"


def test_failure_reports_child_output(runner, monkeypatch):
    monkeypatch.setenv("STUB_FAIL", "1")
    run = runner.start("htdemucs", tone(), background=False)
    with pytest.raises(RuntimeError, match="model exploded"):
        run.result()


def test_cancel_stops_the_child(runner, monkeypatch):
    monkeypatch.setenv("STUB_SLEEP", "1")
    run = runner.start("bs_roformer", tone(), background=False)
    run.cancel()
    assert run.poll() is True


def test_device_and_prepare(runner):
    assert runner.device() == "cpu: stub"
    assert runner.prepare("htdemucs", timeout=30) >= 0.0


@pytest.fixture
def failing_runner(tmp_path):
    stub = tmp_path / "failing_stub.py"
    stub.write_text("import sys\nprint('boom from child', file=sys.stderr)\nsys.exit(1)\n")
    return SubprocessRunner(tmp_path / "models", tmp_path / "work", child_cmd=[sys.executable, str(stub)])


def test_device_failure_message_includes_stderr_tail(failing_runner):
    with pytest.raises(RuntimeError, match="boom from child"):
        failing_runner.device()


def test_prepare_failure_message_includes_stderr_tail(failing_runner):
    with pytest.raises(RuntimeError, match="boom from child"):
        failing_runner.prepare("htdemucs", timeout=30)


def test_clean_work_dir_removes_leftover_run_directories(tmp_path):
    work = tmp_path / ".work"
    (work / "crashed-run").mkdir(parents=True)
    (work / "crashed-run" / "input.wav").write_bytes(b"x")
    (work / "stray.log").write_text("x")
    clean_work_dir(work)
    assert list(work.iterdir()) == []


def test_clean_work_dir_tolerates_a_missing_directory(tmp_path):
    clean_work_dir(tmp_path / "never-created")


@pytest.mark.slow
def test_real_htdemucs_separates_a_clip(tmp_path):
    real = SubprocessRunner(tmp_path / "models", tmp_path / "work")
    run = real.start("htdemucs", tone(6.0), background=False)
    while not run.poll():
        pass
    vocals = run.result()
    assert vocals.shape[0] == 2 and abs(vocals.shape[1] - 6 * SAMPLE_RATE) < SAMPLE_RATE // 10
