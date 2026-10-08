"""US VIRAL portal: private control panel. Phase 1: login (password + 2FA), "Oggi" and "Coda" pages, read-only.
Data comes live from the n8n Data Tables / executions (REST API) and from the media worker's /health."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, config, logic, n8n

BASE = Path(__file__).parent
app = FastAPI(title="US VIRAL portal", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

CSP = ("default-src 'self'; img-src 'self' https: data:; media-src https:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    public = path in ("/login", "/healthz") or path.startswith("/static/")
    if not public and not auth.valid_session(request.cookies.get(auth.COOKIE)):
        if request.method == "GET":
            return RedirectResponse("/login", status_code=303)
        return Response(status_code=401)
    if request.method == "POST":  # cookies are SameSite=Strict; also refuse posts coming from another site
        origin = request.headers.get("origin")
        if origin and origin.split("://")[-1] != request.headers.get("host"):
            return Response("cross-site request refused", status_code=403)
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"  # "no-referrer" makes browsers send Origin: null on posts
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    if not path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------ template helpers
def fmt_n(v) -> str:
    return "—" if v is None else f"{round(v):,}".replace(",", ".")


def when(d: datetime | None) -> str:
    """'15:05 NY · 21:05 IT' (+ day when not today)."""
    if not d:
        return "—"
    ny, it = d.astimezone(logic.NY), d.astimezone(logic.IT)
    today = datetime.now(logic.NY).date()
    day = "" if ny.date() == today else ("domani " if (ny.date() - today).days == 1 else
                                         "ieri " if (today - ny.date()).days == 1 else ny.strftime("%d/%m "))
    return f"{day}{ny:%H:%M} NY · {it:%H:%M} IT"


def ago(d: datetime | None) -> str:
    if not d:
        return "—"
    s = (datetime.now(timezone.utc) - d).total_seconds()
    future, s = s < 0, abs(s)
    txt = (f"{int(s // 60)} min" if s < 3600 else f"{int(s // 3600)}h {int(s % 3600 // 60):02d}m" if s < 86400
           else f"{int(s // 86400)} g {int(s % 86400 // 3600)}h")
    return f"tra {txt}" if future else f"{txt} fa"


def hours(h) -> str:
    if h is None:
        return "—"
    return f"{h:.0f}h" if abs(h) >= 1 else f"{h * 60:.0f} min"


templates.env.filters.update(n=fmt_n, when=when, ago=ago, hours=hours)
templates.env.globals["iso"] = lambda d: d.isoformat() if d else ""


def page(request: Request, name: str, ctx: dict) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"nav": name, **ctx})


# ------------------------------------------------------------------ login
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    if auth.valid_session(request.cookies.get(auth.COOKIE)):
        return RedirectResponse("/", status_code=303)
    # only what blocks the login itself (the n8n status is shown after login, not to strangers)
    blocking = [p for p in config.problems() if p.startswith("PORTAL_")]
    return page(request, "login.html", {"error": error, "problems": blocking,
                                        "totp": bool(config.PORTAL_TOTP_SECRET)})


@app.post("/login")
async def login(request: Request, password: str = Form(""), code: str = Form("")):
    ip = request.client.host if request.client else "?"
    if auth.blocked(ip):
        return RedirectResponse("/login?error=troppi+tentativi:+riprova+tra+15+minuti", status_code=303)
    await asyncio.sleep(0.4)  # slows down guessing
    ok = bool(config.PORTAL_PASSWORD_HASH) and len(config.PORTAL_SECRET) >= 32 \
        and auth.check_password(password, config.PORTAL_PASSWORD_HASH) \
        and (not config.PORTAL_TOTP_SECRET or auth.check_totp(config.PORTAL_TOTP_SECRET, code))
    if not ok:
        auth.record_failure(ip)
        return RedirectResponse("/login?error=password+o+codice+non+validi", status_code=303)
    auth.clear_failures(ip)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_session(), max_age=config.SESSION_DAYS * 86400, httponly=True,
                    secure=config.COOKIE_SECURE, samesite="strict")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.post("/refresh")
def refresh(request: Request):
    n8n.clear_cache()
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


# ------------------------------------------------------------------ data
async def worker_health() -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{config.WORKER_URL}/health")
            return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def load(need_perf: bool) -> dict:
    """Everything a page needs, fetched in parallel. An n8n failure becomes a banner, never a crash."""
    jobs = {
        "config": n8n.table_rows("config", ttl=60),
        "posts": n8n.table_rows("posts", ttl=45),
        "workflows": n8n.workflows(),
        "execs": n8n.executions_since(48),
        "worker": worker_health(),
    }
    if need_perf:
        jobs |= {"runs": n8n.table_rows("runs", since_hours=26, ttl=60),
                 "snaps": n8n.table_rows("post_metrics", since_hours=56, ttl=300),
                 "followers": n8n.table_rows("account_metrics", since_hours=32, ttl=300),
                 "month": n8n.executions_this_month()}
    results = await asyncio.gather(*jobs.values(), return_exceptions=True)
    data, errors = {}, []
    for key, res in zip(jobs, results):
        if isinstance(res, Exception):
            errors.append(str(res) if isinstance(res, n8n.N8nError) else f"{key}: {type(res).__name__}: {res}")
            res = None if key in ("worker", "month") else []
        data[key] = res
    data["errors"] = list(dict.fromkeys(errors))
    now = datetime.now(timezone.utc)
    cfg = logic.load_config(data["config"])
    posts = [logic.card(p, now, cfg) for p in data["posts"] if p.get("post_key")]
    wf = {w["id"]: w for w in data["workflows"]}
    last_exec = {}
    for e in data["execs"]:  # newest first
        e["started"] = logic.parse(e.get("startedAt"))
        if e["workflowId"] in wf and e["workflowId"] not in last_exec:
            last_exec[e["workflowId"]] = e
    factory_id = next((i for i, w in wf.items() if "Factory" in w["name"]), None)
    data.update(now=now, cfg=cfg, posts=posts, wf=wf, last_exec=last_exec,
                factory=logic.factory_state(now, cfg, posts, last_exec.get(factory_id)),
                plan=logic.forecast(now, cfg, posts))
    return data


@app.get("/", response_class=HTMLResponse)
async def today(request: Request):
    d = await load(need_perf=True)
    now, cfg, posts = d["now"], d["cfg"], d["posts"]
    today_ny = logic.ny_day(now)
    published = sorted((p for p in posts if p["status"] == "PUBLISHED" and logic.ny_day(p["published"]) == today_ny),
                       key=lambda p: p["published"])
    scheduled = {e["post"]["post_key"] for e in d["plan"] if e["post"]}
    next_slot = next((e for e in d["plan"] if e["post"]), None)
    fb_waiting = [p for p in posts if p["status"] == "PUBLISHED" and not p.get("fb_video_id")
                  and p["published"] and (now - p["published"]).total_seconds() < 3 * 86400] if logic.num(cfg, "fb_enabled") else []
    wf_names = {i: w["name"] for i, w in d["wf"].items()}
    month_execs = d.get("month")
    return page(request, "today.html", {
        **d, "published": published, "next": next_slot, "fb_waiting": fb_waiting,
        "next_fb": logic.next_fb_run(now),
        "perf": logic.performance(now, posts, d["snaps"], d["followers"]),
        "problems": logic.problems(now, cfg, posts, d["runs"], d["execs"], wf_names, scheduled),
        "cap": int(logic.num(cfg, "publish_max_per_day")), "month_execs": month_execs,
        "month_limit": config.N8N_MONTHLY_EXECUTIONS,
    })


@app.get("/coda", response_class=HTMLResponse)
async def queue(request: Request):
    d = await load(need_perf=False)
    posts, now = d["posts"], d["now"]
    slot_of = {e["post"]["post_key"]: e["at"] for e in d["plan"] if e["post"]}
    ready = sorted((p for p in posts if p["status"] == "READY" and not p["test"]),
                   key=lambda p: (p["post_key"] not in slot_of, slot_of.get(p["post_key"], now), -p["score"]))
    recent = sorted((p for p in posts if p["status"] in ("STALE", "FAILED", "SKIPPED", "PUBLISH_FAILED")
                     and p["updated"] and (now - p["updated"]).total_seconds() < 2 * 86400),
                    key=lambda p: p["updated"], reverse=True)
    tests = [p for p in posts if p["status"] == "READY" and p["test"]]
    return page(request, "queue.html", {**d, "ready": ready, "slot_of": slot_of, "recent": recent, "tests": tests})
