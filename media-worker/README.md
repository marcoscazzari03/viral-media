# US VIRAL — Media Worker

Self-hosted media service for the Reel Factory (n8n Cloud cannot run FFmpeg/Whisper).

- **FastAPI** job API with bearer token
- **Piper** local TTS (English voice `en_US-lessac-medium`)
- **faster-whisper** local transcription with word timestamps (model `small.en`, CPU int8)
- **FFmpeg** 1080x1920 Reel rendering: visuals (image / video / color) + voiceover + burned captions + hook + watermark
- **Caddy** automatic HTTPS, serves finished files publicly (needed by the Instagram API)

Jobs are asynchronous: n8n creates a job, then checks it on a later run (no long Wait nodes,
compatible with the 5-minute execution limit of n8n Cloud).

## API

All endpoints except `/health` need `Authorization: Bearer <API_TOKEN>`.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | liveness, queue size |
| POST | `/jobs` | create job `{ "type": "tts" \| "transcribe" \| "render", "params": {...}, "ref": "youtube:abc" }` → `202 {id, status}` |
| GET | `/jobs/{id}` | `status`: `queued` → `running` → `done` \| `failed`, with `result` / `error` |
| GET | `/files/{id}/...` | public outputs (served by Caddy) |

### `render` params

```json
{
  "voiceover": { "text": "Kai Cenat really thought this was a good idea...", "speed": 1.05 },
  "hook": "Kai Cenat immediately regretted this",
  "hook_seconds": 3,
  "watermark": "@yourpage",
  "captions": true,
  "words_per_caption": 3,
  "max_duration": 90,
  "visuals": [
    { "type": "image", "url": "https://.../image.jpg", "duration": 4 },
    { "type": "video", "url": "https://.../licensed_clip.mp4", "start": 12.5 },
    { "type": "color", "color": "#111111" }
  ]
}
```

Result: `video_url`, `cover_url`, `duration`, `width`, `height`, `size_bytes`.
Use `audio_url` instead of `voiceover` to supply your own audio.

The worker only downloads plain http(s) URLs. **Rights are decided upstream** (`rights_status` in n8n):
only pass media you are allowed to use.

### `transcribe` params

`{ "media_url": "https://.../file.mp4", "language": "en" }` → `segments`, `text`, `transcript_url`.

### `tts` params

`{ "text": "...", "speed": 1.0 }` → `audio_url`, `duration`.

## Install (Ubuntu 24.04, as root)

```bash
git clone -b claude/lucid-fermat-f0ogho https://github.com/marcoscazzari03/viral-media.git /opt/viral-media
cd /opt/viral-media/media-worker
bash scripts/install.sh
```

The script installs Docker, creates `.env` with a random `API_TOKEN` (printed at the end),
builds and starts the containers. Requires the DNS record `media.<domain>` → server IP
and ports 80/443 open, so Caddy can obtain the HTTPS certificate.

## Update

```bash
cd /opt/viral-media && git pull && cd media-worker && docker compose up -d --build
```

## Operations

- Logs: `docker compose logs -f worker`
- Jobs and files older than `RETENTION_DAYS` (default 7) are deleted automatically.
- Settings live in `.env` (never committed).
