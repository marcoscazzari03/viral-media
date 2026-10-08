"""Media worker data read straight from its volume (mounted read-only): job JSONs (status, timings, errors, render
result) and the files served at https://DOMAIN/files/<job_id>/<name>. No copies, no extra API."""
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/srv/media"))
PUBLIC_MEDIA_URL = os.environ.get("PUBLIC_MEDIA_URL", "").rstrip("/")
RETENTION_DAYS = float(os.environ.get("RETENTION_DAYS", "7"))
JOB_ID = re.compile(r"^[a-f0-9]{8,64}$")
FILES_URL = re.compile(r"/files/([a-f0-9]{8,64})/([\w.-]+)$")

_cache: dict[str, tuple[float, object]] = {}


def available() -> bool:
    return (DATA_DIR / "jobs").is_dir()


def _dt(value) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def job(job_id: str | None) -> dict | None:
    """One worker job (transcribe / render / tts) with parsed times and its duration in seconds."""
    if not job_id or not JOB_ID.match(str(job_id)):
        return None
    p = DATA_DIR / "jobs" / f"{job_id}.json"
    try:
        j = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    for k in ("created_at", "started_at", "finished_at"):
        j[k + "_dt"] = _dt(j.get(k))
    s, f = j.get("started_at_dt"), j.get("finished_at_dt")
    j["seconds"] = (f - s).total_seconds() if s and f else None
    return j


def file_state(url: str | None) -> bool | None:
    """True / False: the file behind a /files/ URL is still on disk (old ones are deleted). None: unknown."""
    m = FILES_URL.search(url or "")
    if not m or not available():
        return None
    return (DATA_DIR / "files" / m.group(1) / m.group(2)).is_file()


def public_url(job_id: str, name: str) -> str:
    return f"{PUBLIC_MEDIA_URL}/files/{job_id}/{name}"


def scan(ttl: float = 60) -> list[dict]:
    """Every job folder with its files (size, date, public URL) and job info, newest first."""
    hit = _cache.get("scan")
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    out = []
    files_dir = DATA_DIR / "files"
    if files_dir.is_dir():
        for d in files_dir.iterdir():
            if not d.is_dir() or not JOB_ID.match(d.name):
                continue
            files = []
            for f in d.iterdir():
                if f.is_file():
                    st = f.stat()
                    files.append({"name": f.name, "size": st.st_size, "url": public_url(d.name, f.name),
                                  "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc)})
            j = job(d.name) or {}
            when = j.get("finished_at_dt") or j.get("created_at_dt") or (
                max((f["modified"] for f in files), default=None))
            out.append({"job_id": d.name, "files": sorted(files, key=lambda f: f["name"]), "job": j,
                        "type": j.get("type") or "?", "status": j.get("status") or "?", "ref": j.get("ref"),
                        "when": when, "size": sum(f["size"] for f in files),
                        "deletes_at": when.timestamp() + RETENTION_DAYS * 86400 if when else None})
    out.sort(key=lambda e: e["when"].timestamp() if e["when"] else 0, reverse=True)
    _cache["scan"] = (time.time(), out)
    return out


def recent_jobs(limit: int = 40) -> list[dict]:
    """Latest worker jobs (also those without files, e.g. failed), newest first."""
    jobs_dir = DATA_DIR / "jobs"
    if not jobs_dir.is_dir():
        return []
    paths = sorted(jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [j for j in (job(p.stem) for p in paths) if j]


def disk() -> dict | None:
    try:
        u = shutil.disk_usage(DATA_DIR)
        return {"total": u.total, "used": u.used, "free": u.free, "pct": u.used / u.total * 100}
    except OSError:
        return None
