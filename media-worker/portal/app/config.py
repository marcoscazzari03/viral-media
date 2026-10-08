"""Portal settings, all from the environment (.env next to docker-compose.yml). Secrets never leave the server."""
import os

N8N_BASE_URL = os.environ.get("N8N_BASE_URL", "").rstrip("/")  # e.g. https://<name>.app.n8n.cloud
N8N_API_KEY = os.environ.get("N8N_API_KEY", "")
WORKER_URL = os.environ.get("WORKER_URL", "http://worker:8000").rstrip("/")

PORTAL_SECRET = os.environ.get("PORTAL_SECRET", "")
PORTAL_PASSWORD_HASH = os.environ.get("PORTAL_PASSWORD_HASH", "")
PORTAL_TOTP_SECRET = os.environ.get("PORTAL_TOTP_SECRET", "")
SESSION_DAYS = int(os.environ.get("PORTAL_SESSION_DAYS", "14"))
COOKIE_SECURE = os.environ.get("PORTAL_COOKIE_SECURE", "1") != "0"  # 0 only for local tests over http

# n8n Data Tables of the project (ids are not secret)
TABLES = {
    "config": os.environ.get("TABLE_CONFIG", "K9NPI0QIqVOkuHD1"),
    "posts": os.environ.get("TABLE_POSTS", "Ma6ybDlHtFNlE0BJ"),
    "runs": os.environ.get("TABLE_RUNS", "sZvqRG1Cg9nFH7lR"),
    "post_metrics": os.environ.get("TABLE_POST_METRICS", "9TnEU4uXAH0hvr1f"),
    "account_metrics": os.environ.get("TABLE_ACCOUNT_METRICS", "vqjHMIflVZuF6DRf"),
    "candidates": os.environ.get("TABLE_CANDIDATES", "XPwUBovmuqyubatZ"),
}
# Only workflows whose name contains this are ever shown (the n8n instance has other projects too)
WORKFLOW_MARK = os.environ.get("WORKFLOW_MARK", "US VIRAL |")
N8N_MONTHLY_EXECUTIONS = int(os.environ.get("N8N_MONTHLY_EXECUTIONS", "2500"))


def problems() -> list[str]:
    """Missing settings, shown on the login page instead of a crash."""
    out = []
    if len(PORTAL_SECRET) < 32:
        out.append("PORTAL_SECRET mancante o troppo corto (almeno 32 caratteri)")
    if not PORTAL_PASSWORD_HASH:
        out.append("PORTAL_PASSWORD_HASH mancante (genera con: docker compose run --rm portal python -m app.setup)")
    if not N8N_BASE_URL or not N8N_API_KEY:
        out.append("N8N_BASE_URL / N8N_API_KEY mancanti: i dati non possono essere letti")
    return out


def fixed_costs() -> list[tuple[str, float]]:
    """MONTHLY_FIXED_COSTS="Server Hetzner=5.49;n8n Cloud=24" (EUR per month) for the Costi section."""
    out = []
    for part in os.environ.get("MONTHLY_FIXED_COSTS", "").split(";"):
        name, _, value = part.partition("=")
        try:
            out.append((name.strip(), float(value.replace(",", "."))))
        except ValueError:
            continue
    return [x for x in out if x[0]]
