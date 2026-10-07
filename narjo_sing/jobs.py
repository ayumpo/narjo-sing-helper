from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

PRIORITY = {"now": 0, "next": 1, "batch": 2, "upgrade": 3}
PRIORITY_NAME = {value: name for name, value in PRIORITY.items()}
URGENT_MAX = PRIORITY["next"]

COLUMNS = "id, key, rel_path, duration, priority, model, background, state, progress, eta, error, created, updated"
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, key TEXT NOT NULL, rel_path TEXT NOT NULL, duration REAL NOT NULL,
    priority INTEGER NOT NULL, model TEXT NOT NULL, background INTEGER NOT NULL, state TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0, eta REAL, error TEXT, created REAL NOT NULL, updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_key_state ON jobs(key, state);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state, priority, created);
CREATE TABLE IF NOT EXISTS stem_usage (key TEXT PRIMARY KEY, last_used REAL NOT NULL);
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


class JobStore:
    def __init__(self, db_path: Path, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock:
            self._db.executescript(SCHEMA)

    @staticmethod
    def _job(row) -> Job:
        (job_id, key, rel, duration, priority, model, background, state, progress, eta, error, created,
         updated) = row
        return Job(job_id, key, rel, duration, PRIORITY_NAME[priority], model, bool(background), state,
                   progress, eta, error, created, updated)

    def _one(self, where: str, args: tuple) -> Job | None:
        row = self._db.execute(f"SELECT {COLUMNS} FROM jobs WHERE {where}", args).fetchone()
        return self._job(row) if row else None

    def _set(self, job_id: str, assignments: str, args: tuple) -> None:
        with self._lock:
            self._db.execute(f"UPDATE jobs SET {assignments}, updated = ? WHERE id = ?", args + (self._clock(), job_id))
            self._db.commit()

    def submit(self, key: str, rel_path: str, duration: float, priority: str, model: str, background: bool) -> Job:
        rank = PRIORITY[priority]
        now = self._clock()
        with self._lock:
            existing = self._one("key = ? AND model = ? AND state IN ('queued','running') ORDER BY priority LIMIT 1",
                                 (key, model))
            if existing is not None:
                if rank < PRIORITY[existing.priority]:
                    self._db.execute("UPDATE jobs SET priority = ?, background = ?, updated = ? WHERE id = ?",
                                     (rank, int(background), now, existing.id))
                    self._db.commit()
                    existing = self._one("id = ?", (existing.id,))
                return existing
            job_id = uuid.uuid4().hex
            self._db.execute(f"INSERT INTO jobs ({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (job_id, key, rel_path, duration, rank, model, int(background), "queued", 0.0, None,
                              None, now, now))
            self._db.commit()
            return self._one("id = ?", (job_id,))

    def record_done(self, key: str, rel_path: str, duration: float, priority: str, model: str) -> Job:
        now = self._clock()
        job_id = uuid.uuid4().hex
        with self._lock:
            self._db.execute(f"INSERT INTO jobs ({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (job_id, key, rel_path, duration, PRIORITY[priority], model, 0, "done", 1.0, 0.0, None,
                              now, now))
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

    def mark_running(self, job_id: str) -> None:
        self._set(job_id, "state = 'running', progress = 0", ())

    def update_progress(self, job_id: str, progress: float, eta: float) -> None:
        self._set(job_id, "progress = ?, eta = ?", (progress, eta))

    def finish(self, job_id: str) -> None:
        self._set(job_id, "state = 'done', progress = 1, eta = 0", ())

    def fail(self, job_id: str, error: str) -> None:
        self._set(job_id, "state = 'failed', error = ?", (error,))

    def requeue(self, job_id: str) -> None:
        self._set(job_id, "state = 'queued', progress = 0, eta = NULL", ())

    def recover(self) -> int:
        with self._lock:
            cursor = self._db.execute("UPDATE jobs SET state = 'queued', progress = 0 WHERE state = 'running'")
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
