"""US VIRAL media worker API.

POST /jobs        create a job (tts | transcribe | render)        -> 202 {id, status}
GET  /jobs/{id}   job status and result (poll from n8n, no long waits)
GET  /health      liveness (no auth)
Files are served by Caddy at /files/<job_id>/...
"""

import hmac
import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .jobs import JobStore, start_cleanup, start_worker
from .media import PROCESSORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

API_TOKEN = os.environ.get("API_TOKEN", "")
if len(API_TOKEN) < 32:
    raise RuntimeError("API_TOKEN missing or too short (min 32 chars): run scripts/install.sh or set it in .env")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/srv/media"))
RETENTION_DAYS = float(os.environ.get("RETENTION_DAYS", "7"))
VERSION = "0.1.0"

app = FastAPI(title="US VIRAL media worker", version=VERSION)
store = JobStore(DATA_DIR)


def require_token(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(token, API_TOKEN):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


class JobRequest(BaseModel):
    type: Literal["tts", "transcribe", "render"]
    params: dict = Field(default_factory=dict)
    ref: str | None = Field(default=None, description="caller reference, e.g. candidate_key")


@app.on_event("startup")
def startup() -> None:
    n = store.requeue_pending()
    start_worker(store, PROCESSORS)
    start_cleanup(store, RETENTION_DAYS)
    logging.getLogger("main").info("worker started, %d pending jobs re-queued", n)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION, "queue": store.queue_size()}


@app.post("/jobs", status_code=202, dependencies=[Depends(require_token)])
def create_job(req: JobRequest) -> dict:
    job = store.create(req.type, req.params, req.ref)
    return {"id": job["id"], "status": job["status"], "type": job["type"], "ref": job["ref"]}


@app.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str) -> dict:
    if not job_id.isalnum():
        raise HTTPException(status_code=400, detail="invalid job id")
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job
