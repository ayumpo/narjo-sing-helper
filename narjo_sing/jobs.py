from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PRIORITY = {"now": 0, "next": 1, "batch": 2, "upgrade": 3}
PRIORITY_NAME = {value: name for name, value in PRIORITY.items()}
URGENT_MAX = PRIORITY["next"]
QUALITY_RANK = {"fast": 0, "auto": 1, "best": 2, "both": 3}

COLUMNS = ("id, key, rel_path, duration, priority, model, background, state, progress, eta, error, created, updated, "
           "requested_quality")
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, key TEXT NOT NULL, rel_path TEXT NOT NULL, duration REAL NOT NULL,
    priority INTEGER NOT NULL, model TEXT NOT NULL, background INTEGER NOT NULL, state TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0, eta REAL, error TEXT, created REAL NOT NULL, updated REAL NOT NULL,
    requested_quality TEXT NOT NULL DEFAULT 'auto'
);
CREATE INDEX IF NOT EXISTS jobs_key_state ON jobs(key, state);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state, priority, created);
CREATE TABLE IF NOT EXISTS stem_usage (key TEXT PRIMARY KEY, last_used REAL NOT NULL);
CREATE TABLE IF NOT EXISTS helper_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Job:
    id: str
    key: str
    rel_path: str
    duration: float
    priority: str
    model: str
    background: bool
    state: str
    progress: float
    eta: float | None
    error: str | None
    created: float
    updated: float
    requested_quality: str


@dataclass(frozen=True)
class DoneJob:
    key: str
    rel_path: str
    model: str
    finished: float


class JobStore:
    def __init__(self, db_path: Path, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock:
            self._db.executescript(SCHEMA)
            # Databases made before quality choices lack the column; their jobs were all Automatic.
            if "requested_quality" not in {row[1] for row in self._db.execute("PRAGMA table_info(jobs)")}:
                self._db.execute("ALTER TABLE jobs ADD COLUMN requested_quality TEXT NOT NULL DEFAULT 'auto'")
                self._db.commit()

    @staticmethod
    def _job(row) -> Job:
        (job_id, key, rel, duration, priority, model, background, state, progress, eta, error, created,
         updated, requested_quality) = row
        return Job(job_id, key, rel, duration, PRIORITY_NAME[priority], model, bool(background), state,
                   progress, eta, error, created, updated, requested_quality)

    def _one(self, where: str, args: tuple) -> Job | None:
        row = self._db.execute(f"SELECT {COLUMNS} FROM jobs WHERE {where}", args).fetchone()
        return self._job(row) if row else None

    def _set(self, job_id: str, assignments: str, args: tuple, only_if: str | None = None) -> bool:
        guard = f" AND {only_if}" if only_if else ""
        with self._lock:
            cursor = self._db.execute(f"UPDATE jobs SET {assignments}, updated = ? WHERE id = ?{guard}",
                                      args + (self._clock(), job_id))
            self._db.commit()
            return cursor.rowcount > 0

    def submit(self, key: str, rel_path: str, duration: float, priority: str, model: str, background: bool,
               requested_quality: str = "auto") -> Job:
        rank = PRIORITY[priority]
        now = self._clock()
        with self._lock:
            existing = self._one("key = ? AND model = ? AND state IN ('queued','running') ORDER BY priority LIMIT 1",
                                 (key, model))
            if existing is not None:
                existing_id = existing.id
                if QUALITY_RANK[requested_quality] > QUALITY_RANK[existing.requested_quality]:
                    # The choice that asks for more survives the merge, and then `retarget_queued`.
                    self._db.execute("UPDATE jobs SET requested_quality = ? WHERE id = ?",
                                     (requested_quality, existing_id))
                if rank < PRIORITY[existing.priority]:
                    self._db.execute("UPDATE jobs SET priority = ?, background = ?, updated = ? WHERE id = ?",
                                     (rank, int(background), now, existing_id))
                    self._demote_other_now_jobs(rank, existing_id, now)
                elif rank == PRIORITY["now"]:
                    self._demote_other_now_jobs(rank, existing_id, now)
                self._db.commit()
                return self._one("id = ?", (existing_id,))
            job_id = uuid.uuid4().hex
            self._db.execute(f"INSERT INTO jobs ({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (job_id, key, rel_path, duration, rank, model, int(background), "queued", 0.0, None,
                              None, now, now, requested_quality))
            self._demote_other_now_jobs(rank, job_id, now)
            self._db.commit()
            return self._one("id = ?", (job_id,))

    def _demote_other_now_jobs(self, rank: int, job_id: str, now: float) -> None:
        # "now" is the song the listener is on: a newer one makes the older queued ones look-ahead work.
        if rank != PRIORITY["now"]:
            return
        self._db.execute("UPDATE jobs SET priority = ?, updated = ? WHERE state = 'queued' AND priority = ? AND id != ?",
                         (PRIORITY["next"], now, PRIORITY["now"], job_id))

    def record_done(self, key: str, rel_path: str, duration: float, priority: str, model: str,
                    requested_quality: str = "auto") -> Job:
        now = self._clock()
        job_id = uuid.uuid4().hex
        with self._lock:
            self._db.execute(f"INSERT INTO jobs ({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (job_id, key, rel_path, duration, PRIORITY[priority], model, 0, "done", 1.0, 0.0, None,
                              now, now, requested_quality))
            self._db.commit()
            return self._one("id = ?", (job_id,))

    def next_runnable(self, allow_background: bool) -> Job | None:
        gate = "" if allow_background else " AND background = 0"
        with self._lock:
            return self._one(f"state = 'queued'{gate} ORDER BY priority, created LIMIT 1", ())

    def has_urgent_queued(self) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM jobs WHERE state = 'queued' AND priority <= ? LIMIT 1",
                                    (URGENT_MAX,)).fetchone() is not None

    def has_other_now_queued(self, key: str) -> bool:
        """Whether some other song has a queued `now` request: the one the listener just skipped to."""
        with self._lock:
            return self._db.execute(
                "SELECT 1 FROM jobs WHERE state = 'queued' AND priority = ? AND key != ? LIMIT 1",
                (PRIORITY["now"], key)).fetchone() is not None

    def mark_running(self, job_id: str) -> bool:
        """False when the job was cancelled after the worker picked it."""
        return self._set(job_id, "state = 'running', progress = 0", (), only_if="state = 'queued'")

    def update_progress(self, job_id: str, progress: float, eta: float) -> None:
        self._set(job_id, "progress = ?, eta = ?", (progress, eta), only_if="state = 'running'")

    def finish(self, job_id: str) -> None:
        self._set(job_id, "state = 'done', progress = 1, eta = 0", ())

    def fail(self, job_id: str, error: str) -> None:
        self._set(job_id, "state = 'failed', error = ?", (error,), only_if="state IN ('queued','running')")

    def requeue(self, job_id: str) -> None:
        self._set(job_id, "state = 'queued', progress = 0, eta = NULL", (), only_if="state = 'running'")

    def set_aside(self, job_id: str) -> None:
        """Running only: back to the queue with no progress, a `now` priority lowered to `next` so this job
        can't cut back in front of the song that pre-empted it. A lower priority is left as it is."""
        self._set(job_id, "state = 'queued', progress = 0, eta = NULL, "
                  "priority = CASE priority WHEN ? THEN ? ELSE priority END",
                  (PRIORITY["now"], PRIORITY["next"]), only_if="state = 'running'")

    def cancel_song(self, job_id: str) -> int | None:
        """Cancels every queued or running job for `job_id`'s song, each model and the upgrade too. Returns how many
        jobs it cancelled, or None when the id is unknown."""
        with self._lock:
            job = self._one("id = ?", (job_id,))
            if job is None:
                return None
            cursor = self._db.execute(
                "UPDATE jobs SET state = 'cancelled', updated = ? WHERE key = ? AND state IN ('queued','running')",
                (self._clock(), job.key))
            self._db.commit()
            return cursor.rowcount

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return row is not None and row[0] == "cancelled"

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO helper_state (key, value) VALUES ('paused', ?)",
                             ("1" if paused else "0",))
            self._db.commit()

    def paused(self) -> bool:
        with self._lock:
            row = self._db.execute("SELECT value FROM helper_state WHERE key = 'paused'").fetchone()
            return row is not None and row[0] == "1"

    def retarget_queued(self, choose: Callable[[Job], str | None]) -> int:
        """Points each queued job at `choose(job)`, the model it should use after a model setting or the benchmark
        changed. None drops the job, and so does a change that would duplicate a job already queued or running.
        Returns how many jobs changed."""
        changed = 0
        with self._lock:
            rows = self._db.execute(f"SELECT {COLUMNS} FROM jobs WHERE state = 'queued' ORDER BY created").fetchall()
            for job in map(self._job, rows):
                model = choose(job)
                if model is None:
                    self._db.execute("DELETE FROM jobs WHERE id = ?", (job.id,))
                    changed += 1
                    continue
                if model == job.model:
                    continue
                twin = self._db.execute(
                    "SELECT 1 FROM jobs WHERE key = ? AND model = ? AND state IN ('queued','running') AND id != ?",
                    (job.key, model, job.id)).fetchone()
                if twin is not None:
                    self._db.execute("DELETE FROM jobs WHERE id = ?", (job.id,))
                else:
                    self._db.execute("UPDATE jobs SET model = ?, updated = ? WHERE id = ?",
                                     (model, self._clock(), job.id))
                changed += 1
            self._db.commit()
        return changed

    def recover(self) -> int:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE jobs SET state = 'queued', progress = 0, eta = NULL WHERE state = 'running'")
            self._db.commit()
            return cursor.rowcount

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._one("id = ?", (job_id,))

    def upgrade_pending(self, key: str) -> bool:
        with self._lock:
            return self._db.execute(
                "SELECT 1 FROM jobs WHERE key = ? AND background = 1 AND state IN ('queued','running') LIMIT 1",
                (key,)).fetchone() is not None

    def queue_length(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]

    def queued_and_running(self) -> list[Job]:
        """Running first (there is at most one), then queued in the order the worker will run them."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {COLUMNS} FROM jobs WHERE state IN ('queued','running') "
                "ORDER BY (state != 'running'), priority, created").fetchall()
            return [self._job(r) for r in rows]

    def recent_done(self, limit: int = 50) -> list[DoneJob]:
        """Most recently finished jobs, one row per rel_path (its latest completion)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT key, rel_path, model, updated FROM ("
                "  SELECT key, rel_path, model, updated,"
                "         ROW_NUMBER() OVER (PARTITION BY rel_path ORDER BY updated DESC) AS rn"
                "  FROM jobs WHERE state = 'done'"
                ") WHERE rn = 1 ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()
            return [DoneJob(*r) for r in rows]

    def touch(self, key: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO stem_usage (key, last_used) VALUES (?, ?)", (key, self._clock()))
            self._db.commit()

    def usage(self) -> dict[str, float]:
        with self._lock:
            return dict(self._db.execute("SELECT key, last_used FROM stem_usage").fetchall())

    def active_keys(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT DISTINCT key FROM jobs WHERE state IN ('queued','running')")}
