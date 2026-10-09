import re
import shutil
import subprocess

import pytest
from conftest import FakeRunner, make_flac
from fastapi.testclient import TestClient

from narjo_sing.api import AppContext, create_app
from narjo_sing.config import Settings
from narjo_sing.jobs import JobStore
from narjo_sing.library.index import LibraryIndex
from narjo_sing.status import HelperStatus, ModelTiming
from narjo_sing.tiers import TierPlan
from narjo_sing.worker import Worker

KEY = "test-key"
SONG = {"clientSongId": "s1", "title": "Ante Ti", "durationSeconds": 2.0, "album": "El Me Salvo",
        "albumArtist": "JC Negron", "path": "/music/JC Negron/El Me Salvo/05 - Ante Ti.flac"}

TWO_TIER = TierPlan("two-tier", "htdemucs", "melband_kim", True, False)
FAST_ONLY = TierPlan("fast-only", "htdemucs", None, False, True)
TIMINGS = {"htdemucs": ModelTiming("htdemucs", 0.5, 2.0), "melband_kim": ModelTiming("melband_kim", 17.0, 11.0)}


def build_env(music, stems, plan=TWO_TIER, timings=None):
    make_flac(music / "JC Negron/El Me Salvo/05 - Ante Ti.flac", seconds=2,
              ALBUMARTIST="JC Negron", ALBUM="El Me Salvo", TITLE="Ante Ti")
    settings = Settings.from_env({"SING_MUSIC_DIR": str(music), "SING_STEMS_DIR": str(stems)})
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    store = JobStore(stems / "jobs.sqlite")
    status = HelperStatus(device="cpu: test", plan=plan, timings=dict(TIMINGS if timings is None else timings),
                          benchmarking=False)
    ctx = AppContext(settings, KEY, index, store, status, "0.1.0")
    client = TestClient(create_app(ctx))
    client.headers["X-Narjo-Sing-Key"] = KEY
    worker = Worker(store, index, FakeRunner(), settings, status, sleep=lambda _: None)
    return client, worker, stems, store


@pytest.fixture
def env(music, stems):
    client, worker, stems, _ = build_env(music, stems)
    return client, worker, stems


def test_ping_needs_no_key(env):
    client, _, _ = env
    assert TestClient(client.app).get("/v1/ping").json() == {"ok": True}


def test_health_requires_the_key(env):
    client, _, _ = env
    assert TestClient(client.app).get("/v1/health").status_code == 401
    body = client.get("/v1/health").json()
    assert body["mode"] == "two-tier" and body["fastSecondsPerMinute"] == 30.0 and body["bestSecondsPerMinute"] == 1020.0
    assert body["state"] == "ready" and body["libraryFiles"] == 1 and body["device"] == "cpu: test"


def test_job_lifecycle_and_stem_downloads(env):
    client, worker, _ = env
    created = client.post("/v1/jobs", json={**SONG, "priority": "now"}).json()
    assert created["state"] == "queued" and created["matchedBy"] == "path" and created["etaSeconds"] == 3.0
    job_id = created["jobId"]
    assert client.get(f"/v1/jobs/{job_id}").json()["state"] == "queued"
    worker.step()
    status = client.get(f"/v1/jobs/{job_id}").json()
    assert status["state"] == "done" and status["quality"] == "fast" and status["upgradePending"] is True
    vocals = client.get(f"/v1/jobs/{job_id}/vocals.m4a")
    assert vocals.status_code == 200 and vocals.headers["content-type"] == "audio/mp4"
    etag = vocals.headers["etag"]
    assert client.get(f"/v1/jobs/{job_id}/vocals.m4a", headers={"If-None-Match": etag}).status_code == 304
    ranged = client.get(f"/v1/jobs/{job_id}/instrumental.m4a", headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    assert client.get(f"/v1/jobs/{job_id}/drums.m4a").status_code == 422


def test_existing_stems_answer_done_immediately(env):
    client, worker, _ = env
    client.post("/v1/jobs", json={**SONG, "priority": "now"})
    worker.step()
    again = client.post("/v1/jobs", json={**SONG, "priority": "next"}).json()
    assert again["state"] == "done" and again["etaSeconds"] == 0.0


def test_not_found_and_batch(env):
    client, _, _ = env
    missing = client.post("/v1/jobs", json={**SONG, "title": "Nope", "path": None, "priority": "now"}).json()
    assert missing == {"clientSongId": "s1", "state": "notFound", "searched": ["tags"]}
    batch = client.post("/v1/jobs/batch", json={"songs": [SONG, {**SONG, "clientSongId": "s2", "title": "Nope",
                                                                  "path": None}]}).json()
    assert [b["state"] for b in batch] == ["queued", "notFound"]


def test_evicted_stems_report_expired(env):
    client, worker, stems = env
    job_id = client.post("/v1/jobs", json={**SONG, "priority": "now"}).json()["jobId"]
    worker.step()
    for child in stems.iterdir():
        if child.is_dir() and (child / "meta.json").exists():
            shutil.rmtree(child)
    assert client.get(f"/v1/jobs/{job_id}").json()["state"] == "expired"
    assert client.get(f"/v1/jobs/{job_id}/vocals.m4a").status_code == 404


def test_validation(env):
    client, _, _ = env
    assert client.post("/v1/jobs", json={**SONG, "priority": "whenever"}).status_code == 422
    assert client.get("/v1/jobs/unknown").status_code == 404


def test_docs_endpoints_are_disabled(env):
    client, _, _ = env
    bare = TestClient(client.app)
    assert bare.get("/docs").status_code == 404
    assert bare.get("/redoc").status_code == 404
    assert bare.get("/openapi.json").status_code == 404


def test_non_ascii_key_is_rejected_not_a_500(env):
    client, _, _ = env
    # httpx's own `headers=` dict forces ascii; a raw byte value skips that so the server, not
    # httpx, is what's under test here (a client can send arbitrary UTF-8 header bytes over the wire).
    resp = TestClient(client.app).get("/v1/health", headers={"X-Narjo-Sing-Key": "café-ñ".encode()})
    assert resp.status_code == 401


def test_submission_is_refused_while_status_error_is_set(env):
    client, worker, _ = env
    worker.status.error = "Benchmark failed: htdemucs prepare blew up"
    job_resp = client.post("/v1/jobs", json={**SONG, "priority": "now"})
    assert job_resp.status_code == 503 and worker.status.error in job_resp.json()["detail"]
    batch_resp = client.post("/v1/jobs/batch", json={"songs": [SONG]})
    assert batch_resp.status_code == 503 and worker.status.error in batch_resp.json()["detail"]


def _job_row_count(store) -> int:
    return store._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def test_lookup_reports_ready_songs_in_order_and_creates_no_jobs(env):
    client, worker, _ = env
    client.post("/v1/jobs", json={**SONG, "priority": "now"})
    worker.step()
    unknown = {**SONG, "clientSongId": "s3", "title": "Nope", "path": None}
    before = _job_row_count(worker.store)
    resp = client.post("/v1/stems/lookup", json={"songs": [SONG, unknown]})
    assert resp.status_code == 200
    assert resp.json() == [
        {"clientSongId": "s1", "ready": True, "quality": "fast"},
        {"clientSongId": "s3", "ready": False, "quality": None},
    ]
    assert _job_row_count(worker.store) == before


def test_lookup_reports_not_ready_for_a_matched_song_with_no_stems(env):
    client, _, _ = env
    resp = client.post("/v1/stems/lookup", json={"songs": [SONG]})
    assert resp.json() == [{"clientSongId": "s1", "ready": False, "quality": None}]


def test_lookup_requires_the_key(env):
    client, _, _ = env
    bare = TestClient(client.app)
    assert bare.post("/v1/stems/lookup", json={"songs": [SONG]}).status_code == 401


def test_lookup_rejects_more_than_two_hundred_songs(env):
    client, _, _ = env
    songs = [{**SONG, "clientSongId": f"s{i}"} for i in range(201)]
    assert client.post("/v1/stems/lookup", json={"songs": songs}).status_code == 422
    assert client.post("/v1/stems/lookup", json={"songs": []}).status_code == 422


def test_lookup_works_while_status_error_is_set(env):
    client, worker, _ = env
    client.post("/v1/jobs", json={**SONG, "priority": "now"})
    worker.step()
    worker.status.error = "Benchmark failed: htdemucs prepare blew up"
    resp = client.post("/v1/stems/lookup", json={"songs": [SONG]})
    assert resp.status_code == 200
    assert resp.json() == [{"clientSongId": "s1", "ready": True, "quality": "fast"}]


def test_index_page_needs_no_key_and_has_no_sensitive_text(env):
    client, _, _ = env
    bare = TestClient(client.app)
    resp = bare.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "Narjo Sing helper is running" in body and "0.1.0" in body
    assert KEY not in body
    assert "Ante Ti" not in body and "JC Negron" not in body and str(SONG["path"]) not in body


def test_queue_requires_the_key(env):
    client, _, _ = env
    bare = TestClient(client.app)
    assert bare.get("/v1/queue").status_code == 401


def test_queue_lists_running_and_queued_in_run_order(env):
    client, worker, _ = env
    first = client.post("/v1/jobs", json={**SONG, "priority": "batch"}).json()
    second_path = worker.settings.music_dir / "JC Negron/El Me Salvo/06 - Otra.flac"
    make_flac(second_path, seconds=2, ALBUMARTIST="JC Negron", ALBUM="El Me Salvo", TITLE="Otra")
    worker.index.scan()
    second_song = {**SONG, "clientSongId": "s2", "title": "Otra", "path": str(second_path)}
    second = client.post("/v1/jobs", json={**second_song, "priority": "now"}).json()
    worker.store.mark_running(first["jobId"])
    resp = client.get("/v1/queue")
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert [j["id"] for j in jobs] == [first["jobId"], second["jobId"]]
    assert jobs[0]["state"] == "running" and jobs[1]["state"] == "queued"
    assert jobs[1]["priority"] == "now"


def test_queue_recent_is_deduplicated_by_path(env):
    client, worker, _ = env
    client.post("/v1/jobs", json={**SONG, "priority": "now"})
    worker.step()
    # A second submission of the same song answers "done" immediately (stems already exist),
    # adding a second done row for the same rel_path that recent_done must collapse away.
    client.post("/v1/jobs", json={**SONG, "priority": "now"})
    resp = client.get("/v1/queue")
    assert resp.status_code == 200
    recent = resp.json()["recent"]
    assert len(recent) == 1
    assert recent[0]["relPath"] == "JC Negron/El Me Salvo/05 - Ante Ti.flac"
    assert recent[0]["quality"] == "fast"
    assert recent[0]["model"] == "htdemucs"
    assert isinstance(recent[0]["finished"], float)


LOOKUP_SONG = {k: SONG[k] for k in ("clientSongId", "title", "durationSeconds", "path")}


def queued(client):
    return [(job["priority"], job["model"], job["requestedQuality"]) for job in client.get("/v1/queue").json()["jobs"]]


def test_fast_asks_for_the_fast_model_and_never_a_better_copy(env):
    client, worker, _ = env
    client.post("/v1/jobs", json={**SONG, "priority": "now", "quality": "fast"})
    assert queued(client) == [("now", "htdemucs", "fast")]
    assert client.get("/v1/queue").json()["jobs"][0]["quality"] == "fast"
    worker.step()
    assert queued(client) == []


def test_best_asks_for_the_better_model_straight_away(env):
    client, _, _ = env
    client.post("/v1/jobs", json={**SONG, "priority": "now", "quality": "best"})
    assert queued(client) == [("now", "melband_kim", "best")]
    assert client.get("/v1/queue").json()["jobs"][0]["quality"] == "best"


def test_both_asks_for_fast_now_and_queues_the_better_copy_once(env):
    client, _, _ = env
    client.post("/v1/jobs", json={**SONG, "priority": "now", "quality": "both"})
    client.post("/v1/jobs", json={**SONG, "priority": "now", "quality": "both"})
    assert queued(client) == [("now", "htdemucs", "both"), ("upgrade", "melband_kim", "both")]


def test_batch_both_queues_the_fast_copy_and_the_upgrade(env):
    client, _, _ = env
    client.post("/v1/jobs/batch", json={"songs": [SONG], "quality": "both"})
    assert queued(client) == [("batch", "htdemucs", "both"), ("upgrade", "melband_kim", "both")]


def test_both_queues_the_better_copy_even_on_a_fast_only_server(music, stems):
    client, _, _, _ = build_env(music, stems, plan=FAST_ONLY)
    client.post("/v1/jobs", json={**SONG, "priority": "now", "quality": "both"})
    assert queued(client) == [("now", "htdemucs", "both"), ("upgrade", "melband_kim", "both")]


def test_best_with_only_a_fast_copy_prepares_the_better_one(env):
    client, worker, _ = env
    client.post("/v1/jobs", json={**SONG, "priority": "now", "quality": "fast"})
    worker.step()
    assert client.post("/v1/jobs", json={**SONG, "priority": "now", "quality": "best"}).json()["state"] == "queued"
    assert queued(client) == [("now", "melband_kim", "best")]
    assert client.post("/v1/jobs", json={**SONG, "priority": "next", "quality": "fast"}).json()["state"] == "done"


def test_an_unavailable_better_model_falls_back_to_fast(music, stems):
    client, _, _, _ = build_env(music, stems, timings={**TIMINGS, "melband_kim": ModelTiming("melband_kim", None, 0.0)})
    assert client.get("/v1/health").json()["bestStatus"] == "unavailable"
    client.post("/v1/jobs", json={**SONG, "priority": "now", "quality": "both"})
    assert queued(client) == [("now", "htdemucs", "both")]


def test_lookup_answers_for_the_chosen_quality(env):
    client, worker, _ = env
    client.post("/v1/jobs", json={**SONG, "priority": "now", "quality": "fast"})
    worker.step()
    assert client.post("/v1/stems/lookup", json={"songs": [LOOKUP_SONG], "quality": "best"}).json()[0]["ready"] is False
    assert client.post("/v1/stems/lookup", json={"songs": [LOOKUP_SONG], "quality": "both"}).json()[0]["ready"] is True
    assert client.post("/v1/stems/lookup", json={"songs": [LOOKUP_SONG]}).json()[0]["ready"] is True


def test_health_reports_the_choices_and_the_better_model(env):
    client, _, _ = env
    body = client.get("/v1/health").json()
    assert body["qualityChoices"] is True and body["paused"] is False
    assert body["configuredBestModel"] == "melband_kim" and body["bestStatus"] == "measured"


def test_an_unknown_quality_is_rejected(env):
    client, _, _ = env
    assert client.post("/v1/jobs", json={**SONG, "priority": "now", "quality": "ultra"}).status_code == 422


def test_pause_holds_work_and_resume_releases_it(env):
    client, worker, _ = env
    assert client.post("/v1/queue/pause").json() == {"paused": True}
    created = client.post("/v1/jobs", json={**SONG, "priority": "now"}).json()
    assert worker.step() is False
    status = client.get(f"/v1/jobs/{created['jobId']}").json()
    assert status["state"] == "queued" and status["helperPaused"] is True
    assert client.get("/v1/queue").json()["paused"] is True and client.get("/v1/health").json()["paused"] is True
    assert client.post("/v1/queue/resume").json() == {"paused": False}
    assert worker.step() is True
    assert client.get(f"/v1/jobs/{created['jobId']}").json()["helperPaused"] is False


def test_pause_resume_and_cancel_require_the_key(env):
    client, _, _ = env
    anonymous = TestClient(client.app)
    assert anonymous.post("/v1/queue/pause").status_code == 401
    assert anonymous.post("/v1/queue/resume").status_code == 401
    assert anonymous.post("/v1/jobs/whatever/cancel").status_code == 401


def test_cancel_stops_the_song_and_its_better_copy(env):
    client, _, _ = env
    created = client.post("/v1/jobs", json={**SONG, "priority": "now", "quality": "both"}).json()
    assert client.post(f"/v1/jobs/{created['jobId']}/cancel").json() == {"cancelled": 2}
    assert client.get(f"/v1/jobs/{created['jobId']}").json()["state"] == "cancelled"
    assert queued(client) == []
    assert client.post("/v1/jobs/nope/cancel").status_code == 404
    again = client.post("/v1/jobs", json={**SONG, "priority": "now"}).json()
    assert again["state"] == "queued" and again["jobId"] != created["jobId"]


def test_index_page_has_pause_and_cancel_without_browser_dialogs(env):
    client, _, _ = env
    body = TestClient(client.app).get("/").text
    assert 'id="pause-btn"' in body and "Cancel this song?" in body
    assert "/v1/queue/pause" in body and "/v1/queue/resume" in body and "/cancel" in body
    assert "confirm(" not in body and "alert(" not in body
    assert "Fast version first, better version later" in body


def test_index_page_script_is_valid_javascript(env, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    client, _, _ = env
    body = TestClient(client.app).get("/").text
    script = re.search(r"<script>(.*)</script>", body, re.S).group(1)
    path = tmp_path / "page.js"
    path.write_text(script)
    assert subprocess.run([node, "--check", str(path)], capture_output=True).returncode == 0
