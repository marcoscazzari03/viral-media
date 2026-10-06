"""US VIRAL media worker API.

POST /jobs        create a job (tts | transcribe | render)        -> 202 {id, status}
GET  /jobs/{id}   job status and result (poll from n8n, no long waits)
GET  /health      liveness (no auth)
Files are served by Caddy at /files/<job_id>/...

TikTok (see tiktok.py):
GET  /panel                 publishing page (password login, cookie)
GET  /tiktok/connect        start "Login with TikTok" (panel session)
GET  /tiktok/callback       OAuth redirect URI
POST /tiktok/post           direct post or inbox draft (bearer token, used by n8n)
GET  /tiktok/status/{id}    publish status (bearer token)
GET  /tiktok/creator        creator info: privacy options, limits (bearer token)
"""

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import tiktok
from .jobs import JobStore, start_cleanup, start_worker
from .media import PROCESSORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

API_TOKEN = os.environ.get("API_TOKEN", "")
if len(API_TOKEN) < 32:
    raise RuntimeError("API_TOKEN missing or too short (min 32 chars): run scripts/install.sh or set it in .env")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/srv/media"))
RETENTION_DAYS = float(os.environ.get("RETENTION_DAYS", "7"))
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "")
VERSION = "0.2.0"
PANEL_HTML = (Path(__file__).parent / "panel.html").read_text()

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


# ---------------------------------------------------------------- TikTok publishing
class TikTokPost(BaseModel):
    video_url: str
    mode: Literal["direct", "inbox"] = "direct"
    title: str = Field(default="", max_length=2200)
    privacy_level: str = ""
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    brand_content: bool = False
    brand_organic: bool = False


def tiktok_call(fn, *args, **kwargs):
    if not tiktok.configured():
        raise HTTPException(status_code=503, detail="TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET missing in .env")
    try:
        return fn(*args, **kwargs)
    except tiktok.TikTokError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def do_post(req: TikTokPost) -> dict:
    if req.mode == "inbox":
        publish_id = tiktok_call(tiktok.inbox_upload, req.video_url)
    else:
        publish_id = tiktok_call(tiktok.direct_post, req.video_url, req.title, req.privacy_level, req.disable_comment,
                                 req.disable_duet, req.disable_stitch, req.brand_content, req.brand_organic)
    return {"publish_id": publish_id, "mode": req.mode}


@app.post("/tiktok/post", dependencies=[Depends(require_token)])
def tiktok_post(req: TikTokPost) -> dict:
    return do_post(req)


@app.get("/tiktok/status/{publish_id}", dependencies=[Depends(require_token)])
def tiktok_status(publish_id: str) -> dict:
    return tiktok_call(tiktok.publish_status, publish_id)


@app.get("/tiktok/creator", dependencies=[Depends(require_token)])
def tiktok_creator() -> dict:
    return tiktok_call(tiktok.creator_info)


# Panel: password login -> session cookie (HMAC of the password, so it changes when the password changes)
def panel_session() -> str:
    return hmac.new(API_TOKEN.encode(), f"panel:{PANEL_PASSWORD}".encode(), hashlib.sha256).hexdigest()


def require_panel(panel: str = Cookie(default="")) -> None:
    if not PANEL_PASSWORD or not hmac.compare_digest(panel, panel_session()):
        raise HTTPException(status_code=401, detail="login required")


def set_cookie(resp, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=True, samesite="lax")


@app.get("/panel", response_class=HTMLResponse)
def panel_page() -> str:
    return PANEL_HTML


@app.post("/panel/login")
async def panel_login(request: Request):
    form = parse_qs((await request.body()).decode())
    password = (form.get("password") or [""])[0]
    if not PANEL_PASSWORD or not hmac.compare_digest(password, PANEL_PASSWORD):
        return RedirectResponse("/panel?error=1", status_code=303)
    resp = RedirectResponse("/panel", status_code=303)
    set_cookie(resp, "panel", panel_session(), 30 * 86400)
    return resp


@app.post("/panel/logout")
def panel_logout():
    resp = RedirectResponse("/panel", status_code=303)
    resp.delete_cookie("panel")
    return resp


@app.get("/tiktok/connect", dependencies=[Depends(require_panel)])
def tiktok_connect():
    if not tiktok.configured():
        raise HTTPException(status_code=503, detail="TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET missing in .env")
    state = tiktok.new_state()
    resp = RedirectResponse(tiktok.authorize_url(state), status_code=302)
    set_cookie(resp, "tt_state", state, 600)
    return resp


@app.get("/tiktok/callback", dependencies=[Depends(require_panel)])
def tiktok_callback(code: str = "", state: str = "", error: str = "", tt_state: str = Cookie(default="")):
    if error:
        return RedirectResponse(f"/panel?tiktok_error={error}", status_code=303)
    if not code or not state or not hmac.compare_digest(state, tt_state):
        raise HTTPException(status_code=400, detail="invalid OAuth state")
    tiktok_call(tiktok.exchange_code, code)
    resp = RedirectResponse("/panel?connected=1", status_code=303)
    resp.delete_cookie("tt_state")
    return resp


@app.get("/panel/api/me", dependencies=[Depends(require_panel)])
def panel_me() -> dict:
    st = tiktok.status()
    if st.get("connected"):
        try:
            st["creator"] = tiktok.creator_info()
        except tiktok.TikTokError as e:
            st["creator_error"] = str(e)
    return st


@app.post("/panel/api/disconnect", dependencies=[Depends(require_panel)])
def panel_disconnect() -> dict:
    tiktok.disconnect()
    return {"connected": False}


@app.get("/panel/api/videos", dependencies=[Depends(require_panel)])
def panel_videos(limit: int = 30) -> list:
    """Most recent finished Reels rendered by this worker."""
    jobs = sorted(store.jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in jobs:
        job = json.loads(p.read_text())
        if job.get("type") != "render" or job.get("status") != "done" or not job.get("result"):
            continue
        prm, res = job.get("params") or {}, job["result"]
        out.append({
            "id": job["id"], "ref": job.get("ref"), "created_at": job.get("created_at"),
            "video_url": res.get("video_url"), "cover_url": res.get("cover_url"), "duration": res.get("duration"),
            "text": prm.get("top_text") or prm.get("hook") or "", "credit": prm.get("credit") or "",
        })
        if len(out) >= limit:
            break
    return out


@app.post("/panel/api/post", dependencies=[Depends(require_panel)])
def panel_post(req: TikTokPost) -> dict:
    return do_post(req)


@app.get("/panel/api/status/{publish_id}", dependencies=[Depends(require_panel)])
def panel_status(publish_id: str) -> dict:
    return tiktok_call(tiktok.publish_status, publish_id)
