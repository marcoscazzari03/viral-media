# US VIRAL — Media Worker

Self-hosted media service for the Reel Factory (n8n Cloud cannot run FFmpeg/Whisper).

- **FastAPI** job API with bearer token
- **Kokoro** local TTS (default, natural US voices, e.g. `am_michael`), **Piper** as fallback
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
  "voiceover": { "text": "Kai Cenat really thought this was a good idea...", "voice": "am_michael", "speed": 1.05 },
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

### `render` clip mode

Pass `clip` instead of `visuals`/`voiceover`: only the requested section is downloaded (yt-dlp for
platform URLs). The clip is enlarged (`fg_scale`, sides cropped) over a blurred copy of itself, with a
slow zoom-in and an optional punch zoom at `emphasis_at` (seconds from the segment start). The `intro`
voice-over plays over the first seconds of the moving clip while the original audio is ducked (`duck`).
Captions are big word groups with the spoken word highlighted; the hook shows for `hook_seconds`, then
the watermark takes its place; the Reel ends with a short fade.

```json
{
  "clip": { "url": "https://www.twitch.tv/kaicenat/clip/CLIP_ID", "start": 0, "end": 24.5 },
  "intro": { "text": "Kai Cenat thought this was a good idea...", "voice": "am_adam", "speed": 1.05 },
  "hook": "He really thought this would work",
  "hook_seconds": 2.5,
  "emphasis_at": 14.2,
  "credit": "twitch.tv/kaicenat",
  "watermark": "@yourpage"
}
```

Optional: `fg_scale` (default 1.7), `duck` (default 0.22), `words_per_caption` (default 3), `captions`
(default true; `"intro_only"` captions just our voice-over, for clips with burned-in subtitles), `top_text`
(a short meme-style line shown above the clip for the whole Reel instead of the hook; the watermark then
shows from the start). `intro` is optional: without it the clip plays with its own audio only. Clips are capped at `MAX_CLIP_SECONDS` (default 75).

### `transcribe` params

`{ "source_url": "https://www.youtube.com/watch?v=..." }` (audio only via yt-dlp) or `{ "media_url": "https://.../file.mp4" }` → `segments`, `text`, `transcript_url`.

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
bash /opt/viral-media/media-worker/scripts/update.sh
```

## Operations

- Logs: `docker compose logs -f worker`
- Jobs and files older than `RETENTION_DAYS` (default 7) are deleted automatically.
- Settings live in `.env` (never committed).
