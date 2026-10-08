"""What the dashboard shows, computed from the n8n tables with the same rules the workflows use:
Publisher (03) slot choice and STALE threshold, Factory (02) caps, Daily Report (05) views/followers/earnings."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
IT = ZoneInfo("Europe/Rome")

# Defaults of the workflows' Init nodes: used when a key is missing from viral_config
DEFAULTS = {
    "publish_enabled": 0, "publish_slots_et": "11,15,19", "publish_max_per_day": 1, "publish_min_gap_h": 3,
    "publish_ready_max_age_h": 36, "publish_force_post_key": "",
    "factory_max_reels_per_day": 3, "factory_ready_buffer_max": 3, "factory_force_candidate_key": "",
    "factory_min_views": 250, "factory_creator_cooldown_h": 12, "factory_platforms": "twitch",
    "factory_voice_ratio": 0.5, "factory_max_segment_s": 60, "yt_privacy": "private",
    "fb_force_post_key": "", "yt_force_post_key": "",
    "factory_themes": "yellow,red,blue,green,purple", "factory_force_theme": "",
    "fb_enabled": 0, "yt_enabled": 0, "yt_max_per_day": 2, "llm_model": "",
}
# Workflow schedules (New York time), mirrored from the triggers: used for "next run"
FACTORY_MINUTE = 37            # 02: every 2 hours at :37
PUBLISH_MINUTE = 5             # 03: slot hours at :05
FB_RUNS = [(12, 35), (16, 35), (20, 35)]   # 07
THEMES = {"yellow": "#FFD221", "red": "#FF4545", "blue": "#3DA9FF", "green": "#3DFF8B", "purple": "#B45CFF"}
IN_PROGRESS = ("TRANSCRIBING", "TRANSCRIBED", "SCRIPTED", "RENDERING", "PUBLISHING")
DAY = 24 * 3600


def parse(value) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_config(rows: list[dict]) -> dict:
    cfg = dict(DEFAULTS)
    for r in rows:
        key = str(r.get("key") or "").strip()
        if not key:
            continue
        num = r.get("value_number")
        cfg[key] = num if num not in (None, "") else (r.get("value_string") or "")
    return cfg


def num(cfg: dict, key: str) -> float:
    try:
        return float(cfg.get(key, DEFAULTS.get(key, 0)) or 0)
    except (TypeError, ValueError):
        return float(DEFAULTS.get(key, 0) or 0)


def slots(cfg: dict) -> list[int]:
    out = []
    for s in str(cfg.get("publish_slots_et", "")).split(","):
        try:
            h = int(s.strip())
            if 0 <= h <= 23:
                out.append(h)
        except ValueError:
            pass
    return sorted(set(out))


def is_test(p: dict) -> bool:
    return str(p.get("status_reason") or "").startswith("forced")


def ny_day(d: datetime | None) -> str:
    return d.astimezone(NY).strftime("%Y-%m-%d") if d else ""


def links(p: dict) -> list[dict]:
    """Platforms where the Reel is out, with their public link."""
    out = []
    if p.get("ig_permalink") or p.get("ig_media_id"):
        out.append({"key": "ig", "label": "Instagram", "url": p.get("ig_permalink") or ""})
    if p.get("fb_video_id"):
        out.append({"key": "fb", "label": "Facebook", "url": f"https://www.facebook.com/reel/{p['fb_video_id']}"})
    if p.get("yt_video_id"):
        out.append({"key": "yt", "label": "YouTube", "url": f"https://youtube.com/shorts/{p['yt_video_id']}"})
    return out


def card(p: dict, now: datetime, cfg: dict) -> dict:
    """Post fields the templates use, with dates parsed and derived values."""
    created = parse(p.get("created_at"))
    age_h = (now - created).total_seconds() / 3600 if created else None
    max_age = num(cfg, "publish_ready_max_age_h")
    theme = (p.get("theme") or "").lower()
    style = str(p.get("style_variant") or "")
    return {
        **p,
        "created": created,
        "published": parse(p.get("published_at")),
        "updated": parse(p.get("updated_at")),
        "age_h": age_h,
        "stale_in_h": (max_age - age_h) if age_h is not None and p.get("status") == "READY" else None,
        "theme": theme,
        "theme_hex": THEMES.get(theme, "#777"),
        "voice": "voce" if style.endswith("+voice") else "senza voce" if style.endswith("+novoice") else "",
        "headline": p.get("meme_caption") or p.get("hook") or p.get("title") or "",
        "test": is_test(p),
        "links": links(p),
        "score": round(float(p.get("trend_score") or 0), 1),
    }


# ------------------------------------------------------------------ Publisher (03) forecast
def next_slot_times(now: datetime, cfg: dict, days: int = 3) -> list[datetime]:
    hours = slots(cfg)
    base = now.astimezone(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for d in range(days):
        day = base + timedelta(days=d)
        for h in hours:
            # rebuild in NY time each day so DST changes keep the wall-clock hour
            t = datetime(day.year, day.month, day.day, h, PUBLISH_MINUTE, tzinfo=NY)
            if t > now:
                out.append(t)
    return out


def forecast(now: datetime, cfg: dict, posts: list[dict], count: int = 6) -> list[dict]:
    """Which READY Reel each upcoming slot will publish, with the Publisher's rules: best trend_score first
    (newest on ties), test posts never, STALE after publish_ready_max_age_h, daily cap, minimum gap.
    A forecast: the Factory adds new Reels every 2 hours and a better one can take the slot."""
    max_age = num(cfg, "publish_ready_max_age_h") * 3600
    cap, gap = num(cfg, "publish_max_per_day"), num(cfg, "publish_min_gap_h") * 3600
    published = [p for p in posts if p["status"] == "PUBLISHED" and p["published"]]
    per_day: dict[str, int] = {}
    for p in published:
        per_day[ny_day(p["published"])] = per_day.get(ny_day(p["published"]), 0) + 1
    last = max((p["published"] for p in published), default=None)
    ready = [p for p in posts if p["status"] == "READY" and p.get("video_url") and not p["test"] and p["created"]]
    ready.sort(key=lambda p: (-p["score"], -p["created"].timestamp()))
    enabled = bool(num(cfg, "publish_enabled"))
    out = []
    for t in next_slot_times(now, cfg):
        day = ny_day(t)
        fresh = [p for p in ready if (t - p["created"]).total_seconds() <= max_age]
        entry = {"at": t, "post": None, "reason": ""}
        if not enabled:
            entry["reason"] = "pubblicazione spenta (publish_enabled = 0)"
        elif per_day.get(day, 0) >= cap:
            entry["reason"] = f"limite giornaliero raggiunto ({int(cap)} al giorno)"
        elif last and (t - last).total_seconds() < gap:
            entry["reason"] = f"troppo vicino all'ultimo Reel (minimo {num(cfg, 'publish_min_gap_h'):g} ore)"
        elif not fresh:
            entry["reason"] = "nessun Reel pronto per ora: la Factory ne prepara uno ogni 2 ore"
        else:
            entry["post"] = fresh[0]
            ready.remove(fresh[0])
            per_day[day] = per_day.get(day, 0) + 1
            last = t
        out.append(entry)
        if len([e for e in out if e["post"]]) >= count or (not fresh and enabled and not entry["post"]
                                                             and entry["reason"].startswith("nessun")):
            break  # nothing left to place: the following slots would all say the same
    return out


def next_factory_run(now: datetime) -> datetime:
    t = now.astimezone(NY).replace(minute=FACTORY_MINUTE, second=0, microsecond=0)
    while t <= now or t.hour % 2:
        t += timedelta(hours=1)
    return t


def next_fb_run(now: datetime) -> datetime:
    base = now.astimezone(NY)
    for d in range(2):
        day = base + timedelta(days=d)
        for h, m in FB_RUNS:
            t = datetime(day.year, day.month, day.day, h, m, tzinfo=NY)
            if t > now:
                return t
    return base


# ------------------------------------------------------------------ Factory (02)
def stuck(p: dict, now: datetime) -> bool:
    upd = p.get("updated") or p.get("created")
    return bool(upd) and (now - upd).total_seconds() > 2 * 3600


def factory_state(now: datetime, cfg: dict, posts: list[dict], last_run: dict | None) -> dict:
    made_24h = [p for p in posts if p["created"] and (now - p["created"]).total_seconds() <= DAY
                and not p["test"] and p["status"] != "FAILED"]
    ready = [p for p in posts if p["status"] == "READY" and not p["test"]]
    # a Factory / Publisher run lasts minutes: a Reel "in progress" untouched for 2 hours is a row left half-way
    working = [p for p in posts if p["status"] in IN_PROGRESS and not stuck(p, now)]
    stuck_rows = [p for p in posts if p["status"] in IN_PROGRESS and stuck(p, now)]
    cap, buf = int(num(cfg, "factory_max_reels_per_day")), int(num(cfg, "factory_ready_buffer_max"))
    if working:
        state, tone = f"Al lavoro: {len(working)} Reel in lavorazione", "ok"
    elif len(ready) >= buf:
        state, tone = f"In pausa: {len(ready)} Reel pronti (buffer massimo {buf})", "idle"
    elif len(made_24h) >= cap:
        state, tone = f"In pausa: {len(made_24h)}/{cap} Reel nelle ultime 24 ore", "idle"
    else:
        state, tone = "Attiva: cerca una clip al prossimo giro", "ok"
    if cfg.get("factory_force_candidate_key"):
        state, tone = "Clip di prova forzata impostata (factory_force_candidate_key)", "warn"
    return {"state": state, "tone": tone, "made_24h": len(made_24h), "cap": cap, "ready": len(ready),
            "buffer": buf, "working": working, "stuck": stuck_rows, "next_run": next_factory_run(now), "last_run": last_run}


# ------------------------------------------------------------------ Performance (same maths as the Daily Report)
def performance(now: datetime, posts: list[dict], snaps: list[dict], followers: list[dict]) -> dict:
    by_key = {p["post_key"]: p for p in posts}
    now_s = now.timestamp()
    series: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for s in snaps:
        t = parse(s.get("captured_at"))
        if not s.get("post_key") or t is None or s.get("views") in (None, ""):
            continue
        key = (s.get("platform") or "instagram", s["post_key"])
        series.setdefault(key, []).append((t.timestamp(), float(s["views"] or 0)))

    def views_at(platform: str, post_key: str, pts: list, t: float) -> float:
        before = [v for ts, v in pts if ts <= t]
        if before:
            return max(((ts, v) for ts, v in pts if ts <= t))[1]
        p = by_key.get(post_key, {})
        out = parse(p.get("yt_published_at") if platform == "youtube" else None) or p.get("published")
        if out and out.timestamp() > t:
            return 0.0  # not out yet at t
        return min(pts)[1]  # out but not measured yet: first snapshot

    gains: dict[str, dict] = {}
    per_post: dict[str, float] = {}
    for (platform, post_key), pts in series.items():
        g = gains.setdefault(platform, {"today": 0.0, "yesterday": 0.0})
        d0 = max(0.0, views_at(platform, post_key, pts, now_s) - views_at(platform, post_key, pts, now_s - DAY))
        d1 = max(0.0, views_at(platform, post_key, pts, now_s - DAY) - views_at(platform, post_key, pts, now_s - 2 * DAY))
        g["today"] += d0
        g["yesterday"] += d1
        per_post[post_key] = per_post.get(post_key, 0) + d0

    rows = []
    for platform, label in (("instagram", "Instagram"), ("youtube", "YouTube"), ("facebook", "Facebook"), ("tiktok", "TikTok")):
        fl = sorted(((parse(r.get("captured_at")), r) for r in followers
                     if (r.get("platform") or "instagram") == platform and r.get("followers_count") not in (None, "")
                     and parse(r.get("captured_at"))), key=lambda x: x[0], reverse=True)
        latest = fl[0][1] if fl else None
        day_ago = next((r for t, r in fl if t.timestamp() <= now_s - DAY), None)
        rows.append({
            "platform": platform, "label": label, "views": gains.get(platform, {}).get("today"),
            "followers": int(latest["followers_count"]) if latest else None,
            "followers_delta": int(latest["followers_count"]) - int(day_ago["followers_count"]) if latest and day_ago else None,
            "note": "API non ancora collegata" if platform == "tiktok" else "nessun dato ancora",
        })
    total = sum(g["today"] for g in gains.values())
    total_y = sum(g["yesterday"] for g in gains.values())
    top = sorted(((v, k) for k, v in per_post.items() if v > 0), reverse=True)[:3]
    earn = sorted((r for r in followers if r.get("earnings_usd") not in (None, "") and parse(r.get("captured_at"))),
                  key=lambda r: parse(r["captured_at"]), reverse=True)
    fb_earn = next((float(r["earnings_usd"]) for r in earn if r.get("platform") == "facebook"), None)
    return {"rows": rows, "total": total, "total_y": total_y,
            "pct": round((total - total_y) / total_y * 100) if total_y else None,
            "top": [{"views": v, "post": by_key.get(k)} for v, k in top if by_key.get(k)],
            "earnings_fb": fb_earn}


# ------------------------------------------------------------------ problems of the last 24 hours
def problems(now: datetime, cfg: dict, posts: list[dict], runs: list[dict], execs: list[dict], wf_names: dict,
             scheduled: set) -> list[dict]:
    out = []
    since = now - timedelta(days=1)
    for p in posts:
        upd = p["updated"]
        recent = upd and upd >= since
        if p["status"] in ("FAILED", "PUBLISH_FAILED") and recent:
            what = "Pubblicazione fallita" if p["status"] == "PUBLISH_FAILED" else "Render / trascrizione fallita"
            out.append({"level": "err", "title": f"{what}: {p.get('creator_name')}",
                        "detail": str(p.get("error") or p.get("status_reason") or "")[:300], "at": upd})
        if p["status"] == "STALE" and recent:
            out.append({"level": "warn", "title": f"Reel scaduto (STALE): {p.get('creator_name')}",
                        "detail": p.get("status_reason") or "", "at": upd})
        for plat, field in (("Facebook", "fb_error"), ("YouTube", "yt_error")):
            if p.get(field) and recent:
                out.append({"level": "err", "title": f"{plat}: errore su {p.get('creator_name')}",
                            "detail": str(p[field])[:300], "at": upd})
    for r in runs:
        t = parse(r.get("started_at"))
        if r.get("status") == "ERROR" and t and t >= since:
            out.append({"level": "err", "title": f"Errore nel workflow {r.get('workflow') or ''}".strip(),
                        "detail": str(r.get("errors") or "")[:300], "at": t})
    for e in execs:
        t = parse(e.get("startedAt"))
        if e.get("workflowId") in wf_names and e.get("status") in ("error", "crashed") and t and t >= since:
            out.append({"level": "err", "title": f"Esecuzione fallita: {wf_names[e['workflowId']]}",
                        "detail": f"esecuzione {e.get('id')}", "at": t, "exec": e})
    for p in posts:
        if (p["status"] == "READY" and not p["test"] and p["post_key"] not in scheduled
                and p["stale_in_h"] is not None and p["stale_in_h"] > 0):
            out.append({"level": "warn", "title": f"Diventa STALE tra {p['stale_in_h']:.0f}h: {p.get('creator_name')}",
                        "detail": "nessuno slot libero prima della scadenza: non verrà pubblicato", "at": None})
    if not num(cfg, "publish_enabled"):
        out.append({"level": "warn", "title": "Pubblicazione automatica spenta (publish_enabled = 0)", "detail": "", "at": None})
    for key in ("publish_force_post_key", "factory_force_candidate_key", "fb_force_post_key", "yt_force_post_key", "factory_force_theme"):
        if cfg.get(key):
            out.append({"level": "warn", "title": f"Impostazione di prova attiva: {key}", "detail": str(cfg[key]), "at": None})
    out.sort(key=lambda x: (x["level"] != "err", -(x["at"].timestamp() if x["at"] else 0)))
    return out


# ------------------------------------------------------------------ content list / detail (phase 2)
STATUS_GROUPS = {  # label shown, CSS tone
    "lavorazione": ("in lavorazione", "work"), "pronto": ("pronto", "ready"), "programmato": ("programmato", "plan"),
    "pubblicato": ("pubblicato", "pub"), "fallito": ("fallito", "fail"), "stale": ("stale", "stale"),
    "scartato": ("scartato", "skip"), "prova": ("prova", "test"), "bloccato": ("bloccato", "fail"),
}


def status_group(p: dict, now: datetime, scheduled: set) -> str:
    st = p.get("status")
    if p.get("test") and st == "READY":
        return "prova"
    if st in IN_PROGRESS:
        return "bloccato" if stuck(p, now) else "lavorazione"
    if st == "READY":
        return "programmato" if p["post_key"] in scheduled else "pronto"
    return {"PUBLISHED": "pubblicato", "FAILED": "fallito", "PUBLISH_FAILED": "fallito", "STALE": "stale",
            "SKIPPED": "scartato"}.get(st, "lavorazione")


def total_views(p: dict) -> int:
    return sum(int(float(p.get(k) or 0)) for k in ("last_views", "fb_views", "yt_views"))


def sparks(snaps: list[dict], width: int = 560, height: int = 120) -> dict:
    """Views over time per platform as SVG polyline points (cumulative totals from the Analytics snapshots)."""
    series: dict[str, list[tuple[float, float]]] = {}
    for s in snaps:
        t = parse(s.get("captured_at"))
        if t and s.get("views") not in (None, ""):
            series.setdefault(s.get("platform") or "instagram", []).append((t.timestamp(), float(s["views"])))
    if not series:
        return {}
    t0 = min(t for pts in series.values() for t, _ in pts)
    t1 = max(t for pts in series.values() for t, _ in pts)
    vmax = max(v for pts in series.values() for _, v in pts) or 1
    pad = 6
    out = {"width": width, "height": height, "vmax": vmax, "t0": datetime.fromtimestamp(t0, timezone.utc),
           "t1": datetime.fromtimestamp(t1, timezone.utc), "lines": []}
    for platform, pts in sorted(series.items()):
        pts.sort()
        xy = [(pad + (t - t0) / ((t1 - t0) or 1) * (width - 2 * pad), height - pad - v / vmax * (height - 2 * pad))
              for t, v in pts]
        out["lines"].append({"platform": platform, "points": " ".join(f"{x:.1f},{y:.1f}" for x, y in xy),
                             "last": pts[-1][1]})
    return out


WORKFLOW_SCHEDULES = {  # by the "NN -" prefix of the workflow name (New York time), mirrored from the triggers
    "00": "quando un altro workflow va in errore", "01": "ogni 2 ore (:07)", "02": "ogni 2 ore (:37)",
    "03": "11, 12, 15, 16, 19, 20 (:05)", "04": "ogni 6 ore (:25)", "05": "ogni giorno alle 20:20",
    "06": "12, 16, 20 (:20)", "07": "12, 16, 20 (:35)",
}
