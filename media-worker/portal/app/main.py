"""US VIRAL portal: private control panel (read-only for now): Oggi, Coda, Contenuti (+ Reel page), Media, Factory,
Workflow. Data comes live from the n8n Data Tables / executions (REST API), the media worker's /health and its
volume (mounted read-only: job JSONs and files)."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
import jinja2
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import audit, auth, charts, config, logic, media, n8n, settings_spec

BASE = Path(__file__).parent
app = FastAPI(title="US VIRAL portal", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

DISK_ALERT_PCT = 80
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
    if v is None or isinstance(v, jinja2.Undefined) or v == "":  # missing column / no value yet
        return "—"
    try:
        return f"{round(float(v)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


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


def size(b) -> str:
    if b is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or unit == "TB":
            return f"{b:.0f} {unit}" if unit in ("B", "KB") else f"{b:.1f} {unit}".replace(".", ",")
        b /= 1024


def secs(s) -> str:
    if s is None:
        return "—"
    return f"{s:.0f} s" if s < 90 else f"{s / 60:.1f} min".replace(".", ",")


def hours(h) -> str:
    if h is None:
        return "—"
    return f"{h:.0f}h" if abs(h) >= 1 else f"{h * 60:.0f} min"


templates.env.filters.update(n=fmt_n, when=when, ago=ago, hours=hours, size=size, secs=secs, dt=logic.parse,
                             dt_ts=lambda ts: datetime.fromtimestamp(ts, timezone.utc) if ts else None)
templates.env.globals["groups"] = logic.STATUS_GROUPS
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
    problems = logic.problems(now, cfg, posts, d["runs"], d["execs"], wf_names, scheduled)
    disk = media.disk() if media.available() else None
    if disk and disk["pct"] >= DISK_ALERT_PCT:  # a full disk stops renders and the public files
        problems.insert(0, {"level": "err" if disk["pct"] >= 90 else "warn", "at": None,
                            "title": f"Disco del server pieno al {disk['pct']:.0f}%",
                            "detail": f"{size(disk['free'])} liberi: lancia update.sh (pulisce le immagini Docker vecchie) "
                                      "o abbassa RETENTION_DAYS"})
    return page(request, "today.html", {
        **d, "published": published, "next": next_slot, "fb_waiting": fb_waiting,
        "next_fb": logic.next_fb_run(now),
        "perf": logic.performance(now, posts, d["snaps"], d["followers"]),
        "problems": problems, "disk": disk,
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


# ------------------------------------------------------------------ phase 2: Contenuti, Reel, Media, Factory, Workflow
def scheduled_keys(d: dict) -> set:
    return {e["post"]["post_key"] for e in d["plan"] if e["post"]}


@app.get("/contenuti", response_class=HTMLResponse)
async def contents(request: Request, stato: str = "", streamer: str = "", tema: str = "", social: str = "",
                   q: str = "", ordine: str = "recenti"):
    d = await load(need_perf=False)
    now, sched = d["now"], scheduled_keys(d)
    posts = d["posts"]
    for p in posts:
        p["group"] = logic.status_group(p, now, sched)
        p["views"] = logic.total_views(p)
    counts: dict[str, int] = {}
    for p in posts:
        counts[p["group"]] = counts.get(p["group"], 0) + 1
    sel = posts
    if stato:
        sel = [p for p in sel if p["group"] == stato]
    if streamer:
        sel = [p for p in sel if (p.get("creator_name") or "") == streamer]
    if tema:
        sel = [p for p in sel if p["theme"] == tema]
    if social:
        sel = [p for p in sel if (social == "nessuno" and not p["links"]) or any(l["key"] == social for l in p["links"])]
    if q:
        ql = q.lower()
        sel = [p for p in sel if ql in " ".join(str(p.get(k) or "") for k in
                                                 ("creator_name", "title", "hook", "meme_caption", "caption")).lower()]
    key = {"views": lambda p: -p["views"], "score": lambda p: -p["score"]}.get(
        ordine, lambda p: -(p["created"].timestamp() if p["created"] else 0))
    sel = sorted(sel, key=key)
    streamers = sorted({p.get("creator_name") or "" for p in posts} - {""}, key=str.lower)
    themes = sorted({p["theme"] for p in posts} - {""})
    return page(request, "contents.html", {**d, "items": sel, "counts": counts, "streamers": streamers,
                                           "themes": themes, "f": {"stato": stato, "streamer": streamer, "tema": tema,
                                                                   "social": social, "q": q, "ordine": ordine}})


@app.get("/contenuti/{post_key}", response_class=HTMLResponse)
async def content(request: Request, post_key: str, ok: str = "", err: str = ""):
    d = await load(need_perf=False)
    now, sched = d["now"], scheduled_keys(d)
    p = next((x for x in d["posts"] if x["post_key"] == post_key), None)
    if not p:
        return page(request, "notfound.html", {**d, "what": post_key})
    p["group"] = logic.status_group(p, now, sched)
    p["views"] = logic.total_views(p)
    try:
        snaps = await n8n.table_where("post_metrics", "post_key", post_key, ttl=120)
    except n8n.N8nError as e:
        snaps, d["errors"] = [], d["errors"] + [str(e)]
    snaps = sorted(snaps, key=lambda s: s.get("captured_at") or "")
    latest: dict[str, dict] = {}
    for s in snaps:
        latest[s.get("platform") or "instagram"] = s
    slot = next((e["at"] for e in d["plan"] if e["post"] and e["post"]["post_key"] == post_key), None)
    return page(request, "content.html", {
        **d, "p": p, "slot": slot, "chart": logic.sparks(snaps), "latest": latest,
        "tjob": media.job(p.get("transcribe_job_id")), "rjob": media.job(p.get("render_job_id")),
        "video_on_disk": media.file_state(p.get("video_url")), "cover_on_disk": media.file_state(p.get("cover_url")),
        "retention": media.RETENTION_DAYS, "ok": ok, "err": err,
        "can_first": p["status"] == "READY" and not p["test"], "can_skip": p["status"] == "READY",
        "can_close": p["status"] in logic.IN_PROGRESS and logic.stuck(p, now),
    })


@app.get("/media", response_class=HTMLResponse)
async def media_page(request: Request, tipo: str = "render"):
    d = await load(need_perf=False)
    by_job = {}
    for p in d["posts"]:
        for k in ("render_job_id", "transcribe_job_id"):
            if p.get(k):
                by_job[p[k]] = p
    entries = media.scan()
    for e in entries:
        e["post"] = by_job.get(e["job_id"])
    totals = {}
    for e in entries:
        t = totals.setdefault(e["type"], {"n": 0, "size": 0})
        t["n"] += 1
        t["size"] += e["size"]
    shown = [e for e in entries if not tipo or e["type"] == tipo]
    return page(request, "media.html", {**d, "entries": shown, "totals": totals, "tipo": tipo, "disk": media.disk(),
                                        "mounted": media.available(), "retention": media.RETENTION_DAYS,
                                        "all_size": sum(e["size"] for e in entries)})


@app.get("/factory", response_class=HTMLResponse)
async def factory_page(request: Request):
    d = await load(need_perf=False)
    now = d["now"]
    try:
        eligible = await n8n.table_where("candidates", "status", "ELIGIBLE", ttl=120)
    except n8n.N8nError as e:
        eligible, d["errors"] = [], d["errors"] + [str(e)]
    cfg = d["cfg"]
    cooldown = logic.num(cfg, "factory_creator_cooldown_h") or 12
    used_at: dict[str, datetime] = {}
    for p in d["posts"]:
        if p.get("source_key") and p["created"] and not p["test"]:
            used_at[p["source_key"]] = max(used_at.get(p["source_key"], p["created"]), p["created"])
    min_views = logic.num(cfg, "factory_min_views")
    platforms = [x.strip() for x in str(cfg.get("factory_platforms") or "twitch").split(",") if x.strip()]
    cands = []
    for c in eligible:
        why = []
        if c.get("platform") not in platforms:
            why.append(f"piattaforma {c.get('platform')} non usata dalla Factory")
        if float(c.get("views") or 0) < min_views:
            why.append(f"meno di {min_views:g} views")
        last = used_at.get(c.get("source_key"))
        if last and (now - last).total_seconds() < cooldown * 3600:
            why.append(f"streamer già usato {ago(last)} (pausa {cooldown:g}h)")
        cands.append({**c, "score": round(float(c.get("trend_score") or 0), 1), "why": why,
                      "published": logic.parse(c.get("published_at"))})
    cands.sort(key=lambda c: (bool(c["why"]), -c["score"]))
    week = [p for p in d["posts"] if p["created"] and (now - p["created"]).total_seconds() < 7 * 86400 and not p["test"]]
    per_status: dict[str, int] = {}
    for p in week:
        per_status[p["status"]] = per_status.get(p["status"], 0) + 1
    failures = sorted((p for p in week if p["status"] in ("FAILED", "SKIPPED")), key=lambda p: p["updated"] or now,
                      reverse=True)
    jobs = media.recent_jobs(30)
    return page(request, "factory.html", {**d, "cands": cands, "per_status": per_status, "week_n": len(week),
                                          "failures": failures, "jobs": jobs, "mounted": media.available(),
                                          "cooldown": cooldown, "min_views": min_views})


@app.get("/workflow", response_class=HTMLResponse)
async def workflow_page(request: Request):
    d = await load(need_perf=False)
    now = d["now"]
    try:
        execs = await n8n.executions_since(7 * 24, ttl=300)
        month = await n8n.executions_this_month()
    except n8n.N8nError as e:
        execs, month, d["errors"] = [], None, d["errors"] + [str(e)]
    try:
        runs = await n8n.table_rows("runs", since_hours=7 * 24, ttl=120)
    except n8n.N8nError as e:
        runs, d["errors"] = [], d["errors"] + [str(e)]
    rows = []
    for wid, w in d["wf"].items():
        mine = [e for e in execs if e["workflowId"] == wid]
        for e in mine:
            e["started"] = logic.parse(e.get("startedAt"))
            stop = logic.parse(e.get("stoppedAt"))
            e["seconds"] = (stop - e["started"]).total_seconds() if stop and e["started"] else None
            e["url"] = f"{config.N8N_BASE_URL}/workflow/{wid}/executions/{e['id']}"
        num = w["name"].split(" ")[0]
        rows.append({**w, "num": num, "schedule": logic.WORKFLOW_SCHEDULES.get(num, ""), "execs": mine[:12],
                     "ok": sum(e["status"] == "success" for e in mine),
                     "err": sum(e["status"] in ("error", "crashed") for e in mine),
                     "url": f"{config.N8N_BASE_URL}/workflow/{wid}"})
    rows.sort(key=lambda r: r["name"])
    day = now.day
    days_in_month = ((now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
    projection = round(month / day * days_in_month) if month is not None and day else None
    errors_runs = sorted((r for r in runs if r.get("status") == "ERROR"), key=lambda r: r.get("started_at") or "",
                         reverse=True)
    for r in errors_runs:
        r["at"] = logic.parse(r.get("started_at"))
    return page(request, "workflow.html", {**d, "rows": rows, "month": month, "projection": projection,
                                           "limit": config.N8N_MONTHLY_EXECUTIONS, "errors_runs": errors_runs})


# ------------------------------------------------------------------ phase 4: Settings (writes viral_config)
@app.get("/impostazioni", response_class=HTMLResponse)
async def settings_page(request: Request, ok: str = "", err: str = "", n: int = 0):
    d = await load(need_perf=False)
    cfg = d["cfg"]
    present = {str(r.get("key")) for r in d["config"]}
    values = {k: cfg.get(k, "") for k in settings_spec.FIELDS}
    return page(request, "settings.html", {**d, "groups_spec": settings_spec.GROUPS, "values": values,
                                           "present": present, "themes_spec": settings_spec.THEMES,
                                           "ok": ok, "err": err, "n": n, "history": audit.latest(40)})


@app.post("/impostazioni/{group}")
async def settings_save(request: Request, group: str):
    spec = next((g for g in settings_spec.GROUPS if g[0] == group), None)
    if not spec:
        return RedirectResponse("/impostazioni", status_code=303)
    form = await request.form()
    try:
        current = logic.load_config(await n8n.table_rows("config", ttl=0))
    except n8n.N8nError as e:
        return RedirectResponse(f"/impostazioni?err={quote(str(e))}#{group}", status_code=303)
    # validate the whole group first: nothing is saved if one field is wrong
    try:
        new = {f["key"]: settings_spec.parse(f, form) for f in spec[2]}
    except settings_spec.Invalid as e:
        return RedirectResponse(f"/impostazioni?err={quote(str(e))}#{group}", status_code=303)
    ip = request.client.host if request.client else "?"
    changed = 0
    for key, value in new.items():
        old = current.get(key)
        if key in current and settings_spec.same(old, value):
            continue
        try:
            await n8n.set_config(key, settings_spec.column(settings_spec.FIELDS[key]), value)
        except n8n.N8nError as e:
            audit.record(key, old, value, ip, False, str(e))
            n8n.clear_cache()
            return RedirectResponse(f"/impostazioni?err={quote(str(e))}&n={changed}#{group}", status_code=303)
        audit.record(key, old, value, ip, True)
        changed += 1
    n8n.clear_cache()
    return RedirectResponse(f"/impostazioni?ok={group}&n={changed}#{group}", status_code=303)


# ------------------------------------------------------------------ phase 3: Analytics + Costi
@app.get("/analytics", response_class=HTMLResponse)
async def analytics(request: Request, giorni: int = 14):
    days = giorni if giorni in (7, 14, 30) else 14
    d = await load(need_perf=False)
    now, posts = d["now"], d["posts"]
    hours = days * 24 + 30
    try:
        snaps, foll = await asyncio.gather(n8n.table_rows("post_metrics", since_hours=hours, ttl=600, max_pages=200),
                                           n8n.table_rows("account_metrics", since_hours=hours, ttl=600))
    except n8n.N8nError as e:
        snaps, foll, d["errors"] = [], [], d["errors"] + [str(e)]
    daily = logic.daily_views(now, posts, snaps, days)
    names = dict(logic.PLATFORMS)
    keys = [(k, n) for k, n in logic.PLATFORMS if any(r.get(k) for r in daily)] or logic.PLATFORMS[:3]
    bars = charts.stacked_bars(daily, keys, lambda r: f"{r['day']:%d/%m}")
    start = logic.ny_midnight(now) - timedelta(days=days - 1)
    fser = logic.followers_series(foll)
    fchart = charts.lines(fser, names, start, now)
    fnow = {k: (v[-1][1], v[-1][1] - next((n for t, n in v if t >= start), v[0][1])) for k, v in fser.items() if v}
    window = [p for p in posts if p["status"] == "PUBLISHED" and p["published"] and p["published"] >= start]
    made = [p for p in posts if p["created"] and p["created"] >= start and not p["test"]]
    for p in window:
        p["views"] = logic.total_views(p)
    hour_of = lambda p: f"{p['published'].astimezone(logic.NY):%H}:00 NY"  # noqa: E731
    total = sum(r["total"] for r in daily)
    month_execs = None
    try:
        month_execs = await n8n.executions_this_month()
    except n8n.N8nError:
        pass
    return page(request, "analytics.html", {
        **d, "days": days, "daily": daily, "bars": bars, "keys": keys, "fchart": fchart, "fnow": fnow,
        "names": names, "total": total, "published_n": len(window), "made_n": len(made),
        "avg": (sum(p["views"] for p in window) / len(window)) if window else None,
        "by_streamer": logic.compare(window, lambda p: p.get("creator_name")),
        "by_theme": logic.compare(window, lambda p: p["theme"] or "prima dei temi"),
        "by_voice": logic.compare(window, lambda p: p["voice"] or "?"),
        "by_hour": logic.compare(window, hour_of),
        "top": sorted(window, key=lambda p: -p["views"])[:10],
        "costs": logic.llm_costs(made), "month_execs": month_execs, "month_limit": config.N8N_MONTHLY_EXECUTIONS,
        "fixed": config.fixed_costs(), "track_days": logic.num(d["cfg"], "analytics_track_days") or 7,
        "earn": logic.earnings(foll, start),
    })


# ------------------------------------------------------------------ phase 4b: Fonti (streamers) + actions on Reels
@app.get("/fonti", response_class=HTMLResponse)
async def sources_page(request: Request, ok: str = "", err: str = ""):
    d = await load(need_perf=False)
    try:
        sources = await n8n.table_rows("sources", ttl=60)
    except n8n.N8nError as e:
        sources, d["errors"] = [], d["errors"] + [str(e)]
    stats: dict[str, dict] = {}
    for p in d["posts"]:
        if p["test"] or not p.get("source_key"):
            continue
        s = stats.setdefault(p["source_key"], {"made": 0, "pub": 0, "views": 0, "last": None})
        s["made"] += 1
        if p["status"] == "PUBLISHED":
            s["pub"] += 1
            s["views"] += logic.total_views(p)
        if p["created"] and (s["last"] is None or p["created"] > s["last"]):
            s["last"] = p["created"]
    rows = []
    for src in sources:
        st = stats.get(src.get("source_key"), {"made": 0, "pub": 0, "views": 0, "last": None})
        rows.append({**src, **st, "avg": st["views"] / st["pub"] if st["pub"] else None})
    rows.sort(key=lambda r: (r.get("platform") != "twitch", not r.get("active"), -(r.get("avg") or -1),
                             str(r.get("name") or r.get("source_key")).lower()))
    return page(request, "sources.html", {**d, "sources": rows, "ok": ok, "err": err,
                                          "factory_platforms": str(d["cfg"].get("factory_platforms") or "twitch")})


@app.post("/fonti/{source_key:path}")
async def source_save(request: Request, source_key: str):
    form = await request.form()
    ip = request.client.host if request.client else "?"
    back = f"/fonti?{{}}#s-{quote(source_key)}"
    try:
        sources = await n8n.table_rows("sources", ttl=0)
    except n8n.N8nError as e:
        return RedirectResponse(back.format("err=" + quote(str(e))), status_code=303)
    matches = [s for s in sources if s.get("source_key") == source_key]
    if len(matches) != 1:
        return RedirectResponse(back.format("err=" + quote("fonte non trovata")), status_code=303)
    cur = matches[0]
    try:
        priority = int(form.get("priority", ""))
    except ValueError:
        priority = 0
    if not 1 <= priority <= 5:
        return RedirectResponse(back.format("err=" + quote("priorità: da 1 a 5")), status_code=303)
    new = {"active": form.get("active") == "1", "priority": priority,
           "has_burned_captions": form.get("has_burned_captions") == "1"}
    changed = {k: v for k, v in new.items()
               if not (cur.get(k) == v or (k == "has_burned_captions" and not v and not cur.get(k))
                       or (k == "priority" and settings_spec.same(cur.get(k), v)))}
    if not changed:
        return RedirectResponse(back.format("ok=" + quote(f"{source_key}: nessuna modifica")), status_code=303)
    try:
        await n8n.update_rows("sources", "source_key", source_key, changed)
    except n8n.N8nError as e:
        for k, v in changed.items():
            audit.record(f"fonte {source_key} · {k}", cur.get(k), v, ip, False, str(e))
        return RedirectResponse(back.format("err=" + quote(str(e))), status_code=303)
    for k, v in changed.items():
        audit.record(f"fonte {source_key} · {k}", cur.get(k), v, ip, True)
    return RedirectResponse(back.format("ok=" + quote(f"{cur.get('name') or source_key}: salvato")), status_code=303)


POST_ACTIONS = {
    "primo": "Metti per primo in coda",
    "scarta": "Scarta (non verrà pubblicato)",
    "chiudi": "Chiudi riga bloccata",
}


@app.post("/contenuti/{post_key}/azione")
async def post_action(request: Request, post_key: str):
    form = await request.form()
    action = str(form.get("azione", ""))
    ip = request.client.host if request.client else "?"
    back = f"/contenuti/{quote(post_key)}?{{}}"
    if action not in POST_ACTIONS:
        return RedirectResponse(back.format("err=" + quote("azione non valida")), status_code=303)
    try:
        rows = await n8n.table_rows("posts", ttl=0)
    except n8n.N8nError as e:
        return RedirectResponse(back.format("err=" + quote(str(e))), status_code=303)
    now = datetime.now(timezone.utc)
    cfg = logic.load_config(await n8n.table_rows("config", ttl=60))
    posts = [logic.card(p, now, cfg) for p in rows if p.get("post_key")]
    matches = [p for p in posts if p["post_key"] == post_key]
    if len(matches) != 1:
        return RedirectResponse(back.format("err=" + quote("Reel non trovato")), status_code=303)
    p = matches[0]
    stamp = f"{now.astimezone(logic.IT):%d/%m %H:%M}"
    if action in ("primo", "scarta") and p["status"] != "READY":
        return RedirectResponse(back.format("err=" + quote("possibile solo su un Reel pronto (READY)")), status_code=303)
    if action == "primo":
        if p["test"]:
            return RedirectResponse(back.format("err=" + quote("è un Reel di prova: il Publisher non lo pubblica mai")), status_code=303)
        best = max((x["score"] for x in posts if x["status"] == "READY" and not x["test"] and x["post_key"] != post_key),
                   default=0)
        # the Publisher always takes the highest trend_score among fresh READY Reels: one point above the best
        data = {"trend_score": round(max(best + 1, p["score"]), 1),
                "status_reason": f"messo per primo dal portale il {stamp} (score originale {p['score']})"}
    elif action == "scarta":
        data = {"status": "SKIPPED", "status_reason": f"scartato a mano dal portale il {stamp}"}
    else:
        if p["status"] not in logic.IN_PROGRESS or not logic.stuck(p, now):
            return RedirectResponse(back.format("err=" + quote("possibile solo su una riga bloccata da più di 2 ore")), status_code=303)
        data = {"status": "FAILED", "status_reason": f"riga bloccata chiusa a mano dal portale il {stamp}",
                "error": f"rimasta in {p['status']} senza avanzare"}
    try:
        await n8n.update_rows("posts", "post_key", post_key, data)
    except n8n.N8nError as e:
        audit.record(f"reel {post_key} · {action}", p["status"], data.get("status") or data.get("trend_score"), ip, False, str(e))
        return RedirectResponse(back.format("err=" + quote(str(e))), status_code=303)
    audit.record(f"reel {post_key} · {action}", p["status"] if action != "primo" else p["score"],
                 data.get("status") or data.get("trend_score"), ip, True)
    return RedirectResponse(back.format("ok=" + quote(POST_ACTIONS[action] + ": fatto")), status_code=303)
