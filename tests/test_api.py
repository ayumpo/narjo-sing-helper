import shutil

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


@pytest.fixture
def env(music, stems):
    make_flac(music / "JC Negron/El Me Salvo/05 - Ante Ti.flac", seconds=2,
              ALBUMARTIST="JC Negron", ALBUM="El Me Salvo", TITLE="Ante Ti")
    settings = Settings.from_env({"SING_MUSIC_DIR": str(music), "SING_STEMS_DIR": str(stems)})
    index = LibraryIndex(stems / "index.sqlite", music)
    index.scan()
    store = JobStore(stems / "jobs.sqlite")
    status = HelperStatus(device="cpu: test", plan=TierPlan("two-tier", "htdemucs", "bs_roformer", True, False),
                          timings={"htdemucs": ModelTiming("htdemucs", 0.5, 2.0),
                                   "bs_roformer": ModelTiming("bs_roformer", 17.0, 11.0)},
                          benchmarking=False)
    ctx = AppContext(settings, KEY, index, store, status, "0.1.0")
    client = TestClient(create_app(ctx))
    client.headers["X-Narjo-Sing-Key"] = KEY
    worker = Worker(store, index, FakeRunner(), settings, status, sleep=lambda _: None)
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
