# US Viral Media

Automated system for **@viralstreamersdaily**: finds viral streamer clips, turns them into vertical Reels
(meme line, captions, optional voice) and publishes them on Instagram, Facebook, YouTube Shorts and TikTok.

Built on **n8n Cloud** (orchestration, data tables) plus a self-hosted **media worker** (FFmpeg, faster-whisper,
Kokoro / Piper TTS, TikTok publishing) on a Hetzner VPS at `media.weborastudio.it`.

See [ROADMAP.md](ROADMAP.md) for status and next steps.

## n8n workflows

Exports (backups, importable in n8n) are in [`n8n/workflows/`](n8n/workflows/). Times are New York.

| Workflow | Schedule | What it does |
|---|---|---|
| `00 - US VIRAL \| Error Handler` | on error | Logs failed runs in `viral_runs` and sends a Telegram alert. Error workflow of all the others. |
| `01 - US VIRAL \| Discovery Engine` | every 2 h at :07 | Polls Twitch clips and YouTube sources, scores them (velocity, outperformance, acceleration, engagement, recency, priority), keeps `viral_candidates` up to date (ELIGIBLE / EXPIRED / ...). |
| `02 - US VIRAL \| Reel Factory` | every 2 h at :37 | Picks the best ELIGIBLE clip, transcribes it, one Claude call for meme line (+ key word) / hook / caption / segment, renders the Reel and its designed cover on the media worker. Saves it as READY in `viral_posts`. |
| `03 - US VIRAL \| Publisher` | :05 at 11, 12, 15, 16, 19, 20 | Publishes the best READY Reel on Instagram in the slots `publish_slots_et` (11, 15, 19), with daily cap and min gap. The hour after each slot resumes a video Instagram is still processing. Telegram message on publish / failure. |
| `04 - US VIRAL \| Analytics` | every 6 h at :25 | Instagram insights and YouTube Shorts statistics (views, likes, comments) of the Reels of the last 7 days (`viral_post_metrics`, one row per platform) , Instagram followers and YouTube subscribers (`viral_account_metrics`, one row per platform). Links Shorts uploaded by hand to their Reel (`yt_video_id`) from the channel's latest uploads. |
| `05 - US VIRAL \| Daily Report` | 20:20 | Telegram summary of the day: Reels published, views of the last 24h per platform (vs the day before), follower / subscriber change per platform, top Reel and, when fewer Reels than planned, the likely reasons. |
| `07 - US VIRAL \| Facebook Publisher` | :35 at 12, 16, 20 | Publishes the Reels already on Instagram as Reels on the Facebook Page (Meta does not share API-published posts to the Page). **Inactive** until the Page token credential is set. |
| `06 - US VIRAL \| YouTube Publisher` | :20 at 12, 16, 20 | Uploads the Reels already published on Instagram as YouTube Shorts. **Inactive** until the YouTube API audit is approved. |

If `publish_slots_et` changes, update the schedule of 03 and 06 too.

Execution budget: about 35 production executions a day (about 1,150 a month with 06 active), under the
n8n plan limit of 2,500 a month shared with all other workflows of the instance.

### Data tables (project US Viral Media)

| Table | Content |
|---|---|
| `viral_config` | All settings (key / value_number / value_string): scoring, Factory style, publishing slots, YouTube, Telegram chat. |
| `viral_sources` | Channels watched (Twitch, YouTube), priority, `has_burned_captions`, `caption_band` (optional fixed band of the streamer's own subtitles). With `has_burned_captions` the render finds the streamer's subtitles frame by frame, blurs only those boxes while they are on screen and puts our captions over them for the whole Reel. |
| `viral_candidates` | Every clip found, with score and status. |
| `viral_posts` | Every Reel made: script, style variant, video URL, Instagram / YouTube ids, metrics. |
| `viral_post_metrics`, `viral_account_metrics` | Analytics snapshots. |
| `viral_runs` | One row per run and every error. |

### Credentials (names only, values live in n8n)

`US VIRAL - Instagram`, `US VIRAL - Telegram`, `US VIRAL - YouTube` (custom Google OAuth2 client, uploads),
`US VIRAL - YouTube API Key` (discovery), `US VIRAL - Twitch API`, `US VIRAL - Anthropic`, `US VIRAL - Media Worker`.
Exports keep credential names and ids only; the Telegram chat id is replaced by `YOUR_TELEGRAM_CHAT_ID`.

## Reel style

Clip Reels (1080×1920): the clip in the centre over a blurred copy of itself, slow zoom + a punch zoom on the key
moment, big word-by-word captions. Gaming / reaction clips with the streamer's webcam in a corner get a split layout instead: the server
finds the webcam (OpenCV face detection, trimmed to the overlay's borders), shows it enlarged in a panel under the
texts and the gameplay below it, with a yellow line between (`layout` render param: auto / split / center). Above the clip, the "pop" meme line for the whole Reel: Montserrat Black,
tilted, key word in yellow, yellow swoosh and sparks, then the page handle and the streamer credit. The AI
voice-over plays on about half of the Reels (A/B test, `style_variant`). Each Reel gets a designed Instagram cover
(punch-moment frame full screen, "VIRAL CLIP" badge, pop title, logo, streamer name, handle) laid out for the 3:4
profile grid. Five visual themes (yellow / soft, red / diagonal, blue / blocks, green / circles, purple / meme) change
only the accent colour and the decorations (underline, side marks, background shapes, badge shape, frame); font,
texts, layout and logo never change. The Factory rotates them (`theme` in `viral_posts`): never one of the last 2,
the least used of the last 5 (`factory_themes`, `factory_force_theme` for tests).

## Media worker

[`media-worker/`](media-worker/): FastAPI + Caddy in Docker. Render jobs for the Factory, public `/files/` for the
platforms, TikTok login and publishing page (`/panel`), legal pages (`/legal/`). Setup and update steps in
[media-worker/README.md](media-worker/README.md).

## Platforms

| Platform | How | Status |
|---|---|---|
| Instagram | 03 Publisher (Instagram Graph API) | automatic, 2 Reels a day |
| Facebook Page | 07 Facebook Publisher (Page Reels API) | waiting for the Page token, manual "Share to Facebook" meanwhile |
| YouTube Shorts | 06 YouTube Publisher (YouTube Data API v3) | waiting for API audit, manual upload meanwhile |
| TikTok | worker `/panel` + `/tiktok/post` (Content Posting API) | waiting for app review, drafts / manual upload meanwhile |

No secrets are stored in this repository: keys live in n8n credentials and in the server `.env`.
