from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from .auth import require_key
from .config import Settings
from .jobs import JobStore
from .library.index import LibraryIndex
from .library.matcher import MatchResult, SongQuery, match
from .status import HelperStatus
from .stems import read_meta, song_key, stem_path
from .webui import render_index


class SongRequest(BaseModel):
    clientSongId: str
    title: str
    durationSeconds: float = Field(gt=0)
    path: str | None = None
    albumArtist: str | None = None
    album: str | None = None
    disc: int | None = None
    track: int | None = None


class JobRequest(SongRequest):
    priority: Literal["now", "next", "batch"]


class BatchRequest(BaseModel):
    songs: list[SongRequest]
    priority: Literal["batch"] = "batch"


class LookupRequest(BaseModel):
    songs: list[SongRequest] = Field(min_length=1, max_length=200)


@dataclass
class AppContext:
    settings: Settings
    key: str
    index: LibraryIndex
    store: JobStore
    status: HelperStatus
    version: str


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="Narjo Sing helper", version=ctx.version, docs_url=None, redoc_url=None, openapi_url=None)
    auth = [Depends(require_key(ctx.key))]

    def seconds_per_minute(model: str | None) -> float | None:
        timing = ctx.status.timings.get(model) if model else None
        return round(timing.rt * 60, 1) if timing and timing.rt else None

    def locate(song: SongRequest) -> MatchResult:
        return match(ctx.index, SongQuery(title=song.title, duration=song.durationSeconds, path=song.path,
                                           album_artist=song.albumArtist, album=song.album, disc=song.disc,
                                           track=song.track))

    def submit(song: SongRequest, priority: str) -> dict:
        result = locate(song)
        if result.file is None:
            return {"clientSongId": song.clientSongId, "state": "notFound", "searched": list(result.searched)}
        file = result.file
        key = song_key(file.rel_path, file.size, file.mtime_ns)
        plan = ctx.status.plan
        model = plan.model_for(priority) if plan else ctx.settings.fast_model
        wants_best = priority == "batch" and plan is not None and plan.best_model is not None
        meta = read_meta(ctx.settings.stems_dir, key)
        if meta is not None and (meta.quality == "best" or not wants_best):
            job = ctx.store.record_done(key, file.rel_path, file.duration, priority, model)
        else:
            job = ctx.store.submit(key, file.rel_path, file.duration, priority, model, background=priority == "batch")
        if job.state == "done":
            eta = 0.0
        elif job.eta is not None:
            eta = job.eta
        else:
            eta = round(ctx.status.expected_seconds(model, file.duration), 1)
        return {"clientSongId": song.clientSongId, "jobId": job.id, "state": job.state, "etaSeconds": eta,
                "matchedBy": result.matched_by}

    def job_or_404(job_id: str):
        job = ctx.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        return job

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(render_index(ctx.version))

    @app.get("/v1/ping")
    def ping() -> dict:
        return {"ok": True}

    @app.get("/v1/health", dependencies=auth)
    def health() -> dict:
        plan = ctx.status.plan
        state = "error" if ctx.status.error else ("benchmarking" if ctx.status.benchmarking else "ready")
        return {
            "version": ctx.version, "device": ctx.status.device, "state": state, "error": ctx.status.error,
            "mode": plan.mode if plan else None,
            "fastModel": ctx.settings.fast_model, "bestModel": plan.best_model if plan else None,
            "fastSecondsPerMinute": seconds_per_minute(ctx.settings.fast_model),
            "bestSecondsPerMinute": seconds_per_minute(ctx.settings.best_model),
            "queueLength": ctx.store.queue_length(),
            "backgroundPrepRecommended": plan.background_prep_recommended if plan else False,
            "libraryFiles": ctx.index.count(),
        }

    @app.get("/v1/queue", dependencies=auth)
    def queue() -> dict:
        jobs = [{"id": job.id, "relPath": job.rel_path, "model": job.model, "priority": job.priority,
                 "state": job.state, "progress": round(job.progress, 3), "etaSeconds": job.eta,
                 "created": job.created, "updated": job.updated} for job in ctx.store.queued_and_running()]
        recent = []
        for done in ctx.store.recent_done(limit=50):
            meta = read_meta(ctx.settings.stems_dir, done.key)
            recent.append({"relPath": done.rel_path, "quality": meta.quality if meta else None,
                           "model": done.model, "finished": done.finished})
        return {"jobs": jobs, "recent": recent}

    def refuse_if_broken() -> None:
        if ctx.status.error:
            raise HTTPException(status_code=503, detail=ctx.status.error)

    @app.post("/v1/jobs", dependencies=auth)
    def create_job(request: JobRequest) -> dict:
        refuse_if_broken()
        return submit(request, request.priority)

    @app.post("/v1/jobs/batch", dependencies=auth)
    def create_batch(request: BatchRequest) -> list[dict]:
        refuse_if_broken()
        return [submit(song, "batch") for song in request.songs]

    @app.post("/v1/stems/lookup", dependencies=auth)
    def lookup_stems(request: LookupRequest) -> list[dict]:
        # Read-only: an index lookup plus a meta.json read, no job rows, so stale `status.error` doesn't apply.
        results = []
        for song in request.songs:
            file = locate(song).file
            meta = read_meta(ctx.settings.stems_dir, song_key(file.rel_path, file.size, file.mtime_ns)) \
                if file else None
            results.append({"clientSongId": song.clientSongId, "ready": meta is not None,
                             "quality": meta.quality if meta else None})
        return results

    @app.get("/v1/jobs/{job_id}", dependencies=auth)
    def job_status(job_id: str) -> dict:
        job = job_or_404(job_id)
        meta = read_meta(ctx.settings.stems_dir, job.key)
        state = "expired" if job.state == "done" and meta is None else job.state
        return {"state": state, "progress": round(job.progress, 3), "etaSeconds": job.eta, "error": job.error,
                "quality": meta.quality if meta else None, "upgradePending": ctx.store.upgrade_pending(job.key)}

    @app.get("/v1/jobs/{job_id}/{stem}.m4a", dependencies=auth)
    def stem_file(job_id: str, stem: Literal["vocals", "instrumental"], request: Request):
        job = job_or_404(job_id)
        meta = read_meta(ctx.settings.stems_dir, job.key)
        if meta is None:
            raise HTTPException(status_code=404, detail="No stems for this job; submit the song again")
        etag = f'"{meta.etag}"'
        ctx.store.touch(job.key)
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return FileResponse(stem_path(ctx.settings.stems_dir, meta, stem), media_type="audio/mp4",
                            headers={"ETag": etag, "Cache-Control": "no-cache"})

    return app
