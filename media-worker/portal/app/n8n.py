"""Read-only client for the n8n REST API (X-N8N-API-KEY): Data Tables rows, workflows, executions.
API calls do not count as workflow executions. Every answer is cached for a short time so that opening or
refreshing the dashboard does not hammer n8n."""
import asyncio
import json
import time
from datetime import datetime, timezone

import httpx

from . import config


class N8nError(Exception):
    pass


_cache: dict[str, tuple[float, object]] = {}
_locks: dict[str, asyncio.Lock] = {}


async def cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    async with _locks.setdefault(key, asyncio.Lock()):
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        value = await fn()
        _cache[key] = (time.time(), value)
        return value


def clear_cache() -> None:
    _cache.clear()


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    if not config.N8N_BASE_URL or not config.N8N_API_KEY:
        raise N8nError("n8n non configurato (N8N_BASE_URL / N8N_API_KEY nel .env)")
    try:
        r = await client.get(f"{config.N8N_BASE_URL}/api/v1{path}", params=params,
                             headers={"X-N8N-API-KEY": config.N8N_API_KEY, "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise N8nError(f"n8n non raggiungibile: {type(e).__name__}") from e
    if r.status_code == 401:
        raise N8nError("n8n ha rifiutato la API key (401): controlla N8N_API_KEY")
    if r.status_code >= 400:
        raise N8nError(f"n8n {path.split('?')[0]}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


def _items(body) -> tuple[list, str | None]:
    if isinstance(body, list):
        return body, None
    data = body.get("data", body.get("rows", []))
    if isinstance(data, dict):  # some versions nest {data: {data: [...], nextCursor}}
        return data.get("data", []), data.get("nextCursor")
    return data, body.get("nextCursor")


async def _pages(path: str, params: dict, stop=None, max_pages: int = 50) -> list:
    """All pages of a list endpoint; stop(item) -> True ends early (lists sorted newest first)."""
    out, cursor = [], None
    async with httpx.AsyncClient(timeout=20) as client:
        for _ in range(max_pages):
            q = {**params, **({"cursor": cursor} if cursor else {})}
            items, cursor = _items(await _get(client, path, q))
            for it in items:
                if stop and stop(it):
                    return out
                out.append(it)
            if not cursor or not items:
                break
    return out


def _ts(value) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


async def table_rows(name: str, since_hours: float | None = None, ttl: float = 45, max_pages: int = 50) -> list[dict]:
    """Rows of one project table. since_hours: only rows created in the last N hours (newest first, early stop)."""
    table = config.TABLES[name]

    async def load():
        if since_hours is None:
            return await _pages(f"/data-tables/{table}/rows", {"limit": 100})
        cut = time.time() - since_hours * 3600
        try:
            return await _pages(f"/data-tables/{table}/rows", {"limit": 100, "sortBy": "createdAt:desc"},
                                stop=lambda r: _ts(r.get("createdAt")) < cut, max_pages=max_pages)
        except N8nError:  # sortBy not supported by this n8n version: full read, filter here
            rows = await _pages(f"/data-tables/{table}/rows", {"limit": 100}, max_pages=max_pages)
            return [r for r in rows if _ts(r.get("createdAt")) >= cut]

    return await cached(f"rows:{name}:{since_hours}", ttl, load)


async def table_where(name: str, column: str, value, ttl: float = 60) -> list[dict]:
    """Rows of one table where column == value (filtered by n8n; full read + local filter if not supported)."""
    table = config.TABLES[name]

    async def load():
        flt = json.dumps({"type": "and", "filters": [{"columnName": column, "condition": "eq", "value": value}]})
        try:
            return await _pages(f"/data-tables/{table}/rows", {"limit": 100, "filter": flt})
        except N8nError:
            rows = await _pages(f"/data-tables/{table}/rows", {"limit": 100})
            return [r for r in rows if r.get(column) == value]

    return await cached(f"where:{name}:{column}:{value}", ttl, load)


async def workflows(ttl: float = 300) -> list[dict]:
    """US VIRAL workflows only (other projects of the instance are never shown)."""
    async def load():
        items = await _pages("/workflows", {"limit": 100})
        mine = [{"id": w["id"], "name": w.get("name", ""), "active": bool(w.get("active"))}
                for w in items if config.WORKFLOW_MARK in w.get("name", "")]
        return sorted(mine, key=lambda w: w["name"])
    return await cached("workflows", ttl, load)


async def executions_since(hours: float, ttl: float = 60) -> list[dict]:
    """Executions of the whole instance started in the last N hours (metadata only), newest first."""
    cut = time.time() - hours * 3600

    async def load():
        items = await _pages("/executions", {"limit": 100, "includeData": "false"},
                             stop=lambda e: _ts(e.get("startedAt")) < cut, max_pages=40)
        return [{"id": e.get("id"), "workflowId": e.get("workflowId"), "status": e.get("status")
                 or ("success" if e.get("finished") else "error"), "mode": e.get("mode"),
                 "startedAt": e.get("startedAt"), "stoppedAt": e.get("stoppedAt")} for e in items]
    return await cached(f"executions:{hours}", ttl, load)


async def executions_this_month(ttl: float = 600) -> int:
    """How many executions the instance ran this calendar month (UTC): the n8n Cloud monthly limit."""
    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()

    async def load():
        items = await _pages("/executions", {"limit": 250, "includeData": "false"},
                             stop=lambda e: _ts(e.get("startedAt")) < start, max_pages=40)
        return len(items)
    return await cached("executions_month", ttl, load)


# ------------------------------------------------------------------ writes (Settings page)
async def set_config(key: str, column: str, value) -> dict:
    """Writes one viral_config value (upsert on `key`: the row is created if missing). column is value_number or
    value_string; the other one is set to null so the workflows read the right type. Returns the saved row."""
    if column not in ("value_number", "value_string"):
        raise N8nError(f"colonna non valida: {column}")
    other = "value_string" if column == "value_number" else "value_number"
    body = {"filter": {"type": "and", "filters": [{"columnName": "key", "condition": "eq", "value": key}]},
            "data": {"key": key, column: value, other: None}, "returnData": True}
    if not config.N8N_BASE_URL or not config.N8N_API_KEY:
        raise N8nError("n8n non configurato (N8N_BASE_URL / N8N_API_KEY nel .env)")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{config.N8N_BASE_URL}/api/v1/data-tables/{config.TABLES['config']}/rows/upsert",
                                  json=body, headers={"X-N8N-API-KEY": config.N8N_API_KEY, "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise N8nError(f"n8n non raggiungibile: {type(e).__name__}") from e
    if r.status_code == 403:
        raise N8nError("la API key di n8n non ha il permesso di scrivere nelle Data Tables (403)")
    if r.status_code >= 400:
        raise N8nError(f"salvataggio di {key} non riuscito: HTTP {r.status_code} {r.text[:200]}")
    rows, _ = _items(r.json() if r.content else [])
    row = next((x for x in rows if isinstance(x, dict) and x.get("key") == key), None)
    if row is None or row.get(column) != value:
        raise N8nError(f"salvataggio di {key} non confermato da n8n: {r.text[:200]}")
    _cache.pop("rows:config:None", None)
    return row


async def update_rows(name: str, column: str, value, data: dict) -> list[dict]:
    """Updates the rows of one table where column == value (exactly one expected) and returns them as saved."""
    body = {"filter": {"type": "and", "filters": [{"columnName": column, "condition": "eq", "value": value}]},
            "data": data, "returnData": True}
    if not config.N8N_BASE_URL or not config.N8N_API_KEY:
        raise N8nError("n8n non configurato (N8N_BASE_URL / N8N_API_KEY nel .env)")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.patch(f"{config.N8N_BASE_URL}/api/v1/data-tables/{config.TABLES[name]}/rows/update",
                                   json=body, headers={"X-N8N-API-KEY": config.N8N_API_KEY, "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise N8nError(f"n8n non raggiungibile: {type(e).__name__}") from e
    if r.status_code == 403:
        raise N8nError("la API key di n8n non ha il permesso di modificare le Data Tables (403)")
    if r.status_code >= 400:
        raise N8nError(f"modifica non riuscita: HTTP {r.status_code} {r.text[:200]}")
    rows, _ = _items(r.json() if r.content else [])
    rows = [x for x in rows if isinstance(x, dict)]
    if not rows or any(x.get(k) != v for x in rows for k, v in data.items()):
        raise N8nError(f"modifica non confermata da n8n: {r.text[:200]}")
    clear_cache()
    return rows
