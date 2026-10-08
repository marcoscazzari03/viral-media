"""Settings change history (who / when / old -> new), the only data the portal stores itself: SQLite on the
portal's own volume. Everything else stays in n8n."""
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.environ.get("PORTAL_DATA_DIR", "/srv/portal-data")) / "portal.db"


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS settings_audit (at TEXT, key TEXT, old TEXT, new TEXT, ip TEXT, ok INTEGER, note TEXT)")
    return c


def record(key: str, old, new, ip: str, ok: bool, note: str = "") -> None:
    try:
        with closing(_conn()) as c, c:
            c.execute("INSERT INTO settings_audit VALUES (?,?,?,?,?,?,?)",
                      (datetime.now(timezone.utc).isoformat(), key, "" if old is None else str(old),
                       "" if new is None else str(new), ip, 1 if ok else 0, note[:300]))
    except sqlite3.Error:
        pass  # the history must never block a change


def latest(limit: int = 40) -> list[dict]:
    try:
        with closing(_conn()) as c:
            rows = c.execute("SELECT at, key, old, new, ip, ok, note FROM settings_audit ORDER BY at DESC LIMIT ?",
                             (limit,)).fetchall()
    except sqlite3.Error:
        return []
    return [{"at": datetime.fromisoformat(r[0]), "key": r[1], "old": r[2], "new": r[3], "ip": r[4], "ok": bool(r[5]),
             "note": r[6]} for r in rows]
