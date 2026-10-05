"""Persistent job store + single background worker thread.

Jobs are JSON files in DATA_DIR/jobs, outputs in DATA_DIR/files/<job_id>/.
One job runs at a time (CPU-bound work: whisper, ffmpeg). Queued/running jobs
are re-queued after a restart, so nothing is lost if the container restarts.
"""

import json
import logging
import queue
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("jobs")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, data_dir: Path):
        self.jobs_dir = data_dir / "jobs"
        self.files_dir = data_dir / "files"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.queue: "queue.Queue[str]" = queue.Queue()
        self.lock = threading.Lock()

    def _path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def save(self, job: dict) -> None:
        with self.lock:
            tmp = self._path(job["id"]).with_suffix(".tmp")
            tmp.write_text(json.dumps(job, ensure_ascii=False))
            tmp.replace(self._path(job["id"]))

    def get(self, job_id: str) -> dict | None:
        p = self._path(job_id)
        if not p.exists():
            return None
        return json.loads(p.read_text())

    def create(self, job_type: str, params: dict, ref: str | None) -> dict:
        job = {
            "id": uuid.uuid4().hex,
            "type": job_type,
            "ref": ref,
            "params": params,
            "status": "queued",
            "created_at": now_iso(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        self.save(job)
        self.queue.put(job["id"])
        return job

    def job_dir(self, job_id: str) -> Path:
        d = self.files_dir / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def requeue_pending(self) -> int:
        n = 0
        for p in sorted(self.jobs_dir.glob("*.json"), key=lambda x: x.stat().st_mtime):
            job = json.loads(p.read_text())
            if job["status"] in ("queued", "running"):
                job["status"] = "queued"
                self.save(job)
                self.queue.put(job["id"])
                n += 1
        return n

    def queue_size(self) -> int:
        return self.queue.qsize()

    def cleanup(self, retention_days: float) -> int:
        cutoff = time.time() - retention_days * 86400
        removed = 0
        for p in self.jobs_dir.glob("*.json"):
            if p.stat().st_mtime < cutoff:
                job = json.loads(p.read_text())
                if job["status"] in ("done", "failed"):
                    shutil.rmtree(self.files_dir / job["id"], ignore_errors=True)
                    p.unlink(missing_ok=True)
                    removed += 1
        return removed


def start_worker(store: JobStore, processors: dict) -> None:
    def loop():
        while True:
            job_id = store.queue.get()
            job = store.get(job_id)
            if not job or job["status"] not in ("queued", "running"):
                continue
            job["status"] = "running"
            job["started_at"] = now_iso()
            store.save(job)
            log.info("job %s (%s) started", job_id, job["type"])
            try:
                result = processors[job["type"]](store.job_dir(job_id), job["params"], job_id)
                job.update(status="done", result=result, finished_at=now_iso())
                log.info("job %s done", job_id)
            except Exception as e:  # noqa: BLE001 - any failure must end the job, not the thread
                job.update(status="failed", error=str(e)[:1500], finished_at=now_iso())
                log.error("job %s failed: %s\n%s", job_id, e, traceback.format_exc())
            store.save(job)

    threading.Thread(target=loop, name="job-worker", daemon=True).start()


def start_cleanup(store: JobStore, retention_days: float) -> None:
    def loop():
        while True:
            try:
                n = store.cleanup(retention_days)
                if n:
                    log.info("cleanup removed %d old jobs", n)
            except Exception as e:  # noqa: BLE001
                log.error("cleanup failed: %s", e)
            time.sleep(3600)

    threading.Thread(target=loop, name="cleanup", daemon=True).start()
