"""Server-side SVG geometry for the Analytics charts (no JS library, CSP-friendly). Templates draw the shapes;
colors come from CSS classes per platform. Hover text lives in data-tip attributes (static/app.js)."""
import math
from datetime import datetime

W, H = 720, 230
LEFT, RIGHT, TOP, BOTTOM = 46, 8, 10, 24


def nice_max(v: float) -> float:
    if v <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 4, 6, 8, 10):  # each divides into 4 round ticks
        if v <= m * exp:
            return m * exp
    return 10 * exp


def fmt(v: float) -> str:
    if v >= 1_000_000:
        return f"{v / 1e6:.1f}M".replace(".0M", "M").replace(".", ",")
    if v >= 10_000:
        return f"{v / 1e3:.0f}k"
    if v >= 1000:
        return f"{v / 1e3:.1f}k".replace(".0k", "k").replace(".", ",")
    return f"{v:.0f}"


def _axis(vmax: float) -> tuple[float, list[dict]]:
    top = nice_max(vmax)
    plot_h = H - TOP - BOTTOM
    ticks = [{"y": TOP + plot_h - plot_h * i / 4, "label": fmt(top * i / 4)} for i in range(5)]
    return top, ticks


def _top_rounded(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    r = min(r, w / 2, h)
    return (f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + w - r:.1f} "
            f"Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z")


def stacked_bars(rows: list[dict], keys: list[tuple[str, str]], labeler) -> dict:
    """rows: [{"day": datetime, key: value, ...}] -> bars with stacked segments (bottom = first key)."""
    vmax = max((sum(r.get(k, 0) for k, _ in keys) for r in rows), default=0)
    top, ticks = _axis(vmax)
    plot_w, plot_h = W - LEFT - RIGHT, H - TOP - BOTTOM
    slot = plot_w / max(len(rows), 1)
    bw = max(4.0, min(28.0, slot * 0.62))
    every = max(1, math.ceil(len(rows) / 10))  # at most ~10 x labels
    bars = []
    for i, r in enumerate(rows):
        x = LEFT + i * slot + (slot - bw) / 2
        y = TOP + plot_h
        segs = []
        present = [(k, r.get(k, 0)) for k, _ in keys if r.get(k, 0) > 0]
        for j, (k, v) in enumerate(present):
            h = v / top * plot_h
            gap = 2 if j < len(present) - 1 and h > 3 else 0  # 2px surface gap between stacked fills
            y -= h
            last = j == len(present) - 1
            segs.append({"key": k, "d": _top_rounded(x, y, bw, h - gap) if last else
                         f"M{x:.1f},{y + h - gap:.1f} V{y:.1f} H{x + bw:.1f} V{y + h - gap:.1f} Z"})
        tip = labeler(r) + " · " + " · ".join(f"{name} {fmt(r.get(k, 0))}" for k, name in keys if r.get(k, 0)) \
            if any(r.get(k, 0) for k, _ in keys) else labeler(r) + " · nessuna view"
        bars.append({"segs": segs, "hit_x": LEFT + i * slot, "hit_w": slot, "tip": tip,
                     # counted back from today, so today is always labelled and labels never collide
                     "label": labeler(r) if (len(rows) - 1 - i) % every == 0 else "", "lx": x + bw / 2})
    return {"w": W, "h": H, "left": LEFT, "right": W - RIGHT, "base": TOP + plot_h, "ticks": ticks, "bars": bars,
            "empty": vmax <= 0}


def lines(series: dict[str, list[tuple[datetime, float]]], names: dict[str, str], t0: datetime, t1: datetime) -> dict:
    """series: key -> [(time, value)] drawn on one shared axis (same unit)."""
    vmax = max((v for pts in series.values() for _, v in pts), default=0)
    top, ticks = _axis(vmax)
    plot_w, plot_h = W - LEFT - RIGHT, H - TOP - BOTTOM
    span = (t1 - t0).total_seconds() or 1
    out = []
    for key, pts in series.items():
        xy = [(LEFT + (t - t0).total_seconds() / span * plot_w, TOP + plot_h - v / top * plot_h, t, v) for t, v in pts
              if t0 <= t <= t1]
        if not xy:
            continue
        out.append({"key": key, "name": names.get(key, key), "points": " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in xy),
                    "last": xy[-1], "dots": [{"x": x, "y": y, "tip": f"{names.get(key, key)} {v:.0f} · {t:%d/%m %H:%M} UTC"}
                                              for x, y, t, v in xy]})
    return {"w": W, "h": H, "left": LEFT, "right": W - RIGHT, "base": TOP + plot_h, "ticks": ticks, "lines": out,
            "empty": not out}
