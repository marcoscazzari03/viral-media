# US VIRAL control panel (portal)

Private dashboard for the whole US VIRAL pipeline, on its own subdomain (default `dashboard.weborastudio.it`).
FastAPI + server-rendered pages, no database for the content: everything is read live from the existing sources.

| Data | Source |
|---|---|
| Reels, statuses, captions, themes, links per platform | n8n Data Table `viral_posts` (REST API) |
| Settings (slots, caps, STALE threshold, themes…) | n8n Data Table `viral_config` |
| Views / followers / earnings | `viral_post_metrics`, `viral_account_metrics` (same maths as the Daily Report) |
| Workflow runs and errors | n8n executions + workflows (REST API, only workflows named `… US VIRAL | …`), `viral_runs` |
| Video / cover files | public URLs of the media worker (`/files/<job>/…`) |
| Media worker queue | `http://worker:8000/health` (internal network) |
| Worker jobs (timings, errors, layout) and files on disk | the media volume, mounted read-only at `/srv/media` |

n8n REST API calls do not count as workflow executions. Answers are cached 45 s – 5 min; the ↻ button reloads.

## Pages (phase 1, read-only)
- **Oggi**: next publication (countdown), published today x/N, Factory state, errors of the last 24 h, views and
  followers of the last 24 h per platform, earnings, top Reels, n8n workflows (last run), executions this month,
  media worker status.
- **Coda**: forecast of the next slots with the Publisher's rules (best score, daily cap, minimum gap, STALE
  threshold, test posts excluded), READY Reels with time left before STALE, Reels in progress, recent failures.

## Pages (phase 2, read-only)
- **Contenuti**: every Reel with status (in lavorazione / pronto / programmato / pubblicato / fallito / stale /
  scartato / prova / bloccato), filters by status, streamer, theme, platform, text; sort by date, views, score.
  Reel page: video + cover (with public URLs to copy), Twitch clip, platform links, timeline (transcription and
  render jobs with duration, layout webcam or centred, errors), stats per platform with a views-over-time chart
  from `viral_post_metrics`, all texts (meme line, hook, voice intro, caption, transcript).
- **Factory**: state, caps, ELIGIBLE candidates and why each one is blocked, statuses of the last 7 days, render /
  transcription errors, latest media worker jobs.
- **Media**: every job folder on the domain with files, sizes, public URLs, linked Reel, deletion date
  (RETENTION_DAYS), server disk usage.
- **Workflow**: each US VIRAL workflow with schedule, last executions (link to n8n), errors in 7 days, executions of
  the month and end-of-month projection against the plan limit, errors logged in `viral_runs`.

## Security
- One admin: scrypt password hash + TOTP (Google Authenticator), 5 wrong attempts per IP = 15 min block.
- Signed session cookie (HttpOnly, Secure, SameSite=Strict, 14 days). A new password logs everyone out.
- n8n API key and secrets only in the server `.env`; strict CSP, `noindex`, no API docs exposed.

## Setup on the server
1. DNS: A record `dashboard.weborastudio.it` → the server IP (the same as `media.weborastudio.it`).
2. n8n: Settings → n8n API → Create an API key.
3. On the server:
   ```bash
   cd /opt/viral-media/media-worker
   bash scripts/update.sh                               # pulls the code, builds worker + portal
   docker compose run --rm portal python -m app.setup   # asks the password, prints the .env lines + 2FA QR
   nano .env                                            # paste PORTAL_* lines, N8N_BASE_URL, N8N_API_KEY
   docker compose up -d portal                          # restarts only the portal with the new settings
   ```
4. Open https://dashboard.weborastudio.it

## Local test
`PORTAL_COOKIE_SECURE=0` allows the session cookie over plain http (never on the server).
